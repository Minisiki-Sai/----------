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


def _body(packet: object, decoded: object) -> bytes | None:
    payload = bytes(getattr(packet, "payload", b""))
    marker_offset = int(getattr(decoded, "record_offset", 0) or 0)
    source_actor_id = int(getattr(decoded, "source_actor_id", 0) or 0)
    damage = int(getattr(decoded, "damage", 0) or 0)
    for phase in _v576_candidate_xor_phases(payload):
        candidate = _decode_v576_body(payload, phase)
        if marker_offset < 0 or marker_offset + 27 > len(candidate):
            continue
        if candidate[marker_offset : marker_offset + 3] != b"\x08\x73\x00":
            continue
        if int.from_bytes(
            candidate[marker_offset + 8 : marker_offset + 12], "little"
        ) != source_actor_id:
            continue
        if int.from_bytes(
            candidate[marker_offset + 19 : marker_offset + 27], "little"
        ) != damage:
            continue
        return candidate
    return None


def audit_capture(path: Path, samples_per_shape: int) -> dict[str, Any]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original_feed = decoder.feed
    catalog = frozenset(int(value) for value in decoder.damage_skill_ids)
    shapes: dict[tuple[str, int, int], dict[str, Any]] = {}

    def feed(packet, packet_features=None):
        event = original_feed(packet, packet_features)
        decoded = decoder.last_decoded_message
        layout = str(getattr(decoded, "layout", ""))
        if (
            not isinstance(event, CombatEvent)
            or event.healing
            or decoded is None
            or "direct37" not in layout
        ):
            return event
        body = _body(packet, decoded)
        if body is None:
            return event
        marker = int(getattr(decoded, "record_offset", 0) or 0)
        signature = bytes(packet.payload[5:9]).hex()
        key = (signature, len(packet.payload), marker)
        row = shapes.setdefault(
            key,
            {
                "packet_signature": signature,
                "payload_length": len(packet.payload),
                "record_offset": marker,
                "rows": 0,
                "exact_rows": 0,
                "assigned_methods": Counter(),
                "exact_skills": Counter(),
                "samples": [],
            },
        )
        row["rows"] += 1
        exact = bool(getattr(decoded, "skill_id_exact", False))
        row["exact_rows"] += int(exact)
        row["assigned_methods"][str(event.attribution_method)] += 1
        if exact:
            row["exact_skills"][str(getattr(decoded, "skill_id", 0) or 0)] += 1
        if len(row["samples"]) < samples_per_shape * 2:
            occurrences: list[dict[str, int]] = []
            stop = min(len(body), marker + 220)
            for offset in range(max(0, marker - 18), max(0, stop - 3)):
                value = int.from_bytes(body[offset : offset + 4], "little")
                if value in catalog:
                    occurrences.append(
                        {"relative_offset": offset - marker, "skill_id": value}
                    )
            wanted_exact = sum(
                int(sample["wire_skill_exact"]) for sample in row["samples"]
            ) < samples_per_shape
            wanted_nonexact = (
                len(row["samples"])
                - sum(int(sample["wire_skill_exact"]) for sample in row["samples"])
                < samples_per_shape
            )
            if (exact and wanted_exact) or (not exact and wanted_nonexact):
                start = max(0, marker - 18)
                row["samples"].append(
                    {
                        "wire_skill_exact": exact,
                        "wire_skill_id": int(getattr(decoded, "skill_id", 0) or 0),
                        "assigned_skill_id": str(event.skill_id),
                        "assigned_skill_name": str(event.skill_name),
                        "assigned_method": str(event.attribution_method),
                        "source_id": str(event.source_id),
                        "timestamp_ms": int(event.timestamp_ms),
                        "known_skill_occurrences": occurrences,
                        "relative_start": start - marker,
                        "relative_hex": body[start:stop].hex(" "),
                    }
                )
        return event

    decoder.feed = feed
    try:
        source.poll()
    finally:
        source.close()

    rendered = []
    for row in shapes.values():
        rendered.append(
            {
                **row,
                "assigned_methods": dict(row["assigned_methods"].most_common()),
                "exact_skills": dict(row["exact_skills"].most_common()),
            }
        )
    rendered.sort(key=lambda row: (-int(row["rows"]), row["packet_signature"]))
    return {"capture": str(path), "shapes": rendered}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--samples-per-shape", type=int, default=2)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    reports = [
        audit_capture(path.resolve(), max(1, args.samples_per_shape))
        for path in args.captures
    ]
    result = {"schema_version": 1, "reports": reports}
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
