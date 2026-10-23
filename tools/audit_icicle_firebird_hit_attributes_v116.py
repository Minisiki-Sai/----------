"""Compare fixed direct37 Hit fields for Ice Injection and Fire Bird.

This audit uses only hits whose skill is already established by an exact
Projectile/object lineage.  It does not infer a skill from timing, damage, or
the current display label.  The output is intended to answer whether a stable
per-Hit wire attribute can safely disambiguate visually similar projectiles.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.protocol_v576 import (  # noqa: E402
    _decode_v576_body,
)
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)


SKILLS = {2207: "Fire Bird", 2353: "Icicle Injection"}
TEMPLATES = {3001, 3021}


def _decoded_record(kwargs: dict[str, Any], owner: int, obj: int) -> bytes | None:
    payload = kwargs["payload"]
    marker = int(kwargs.get("record_offset", 0))
    damage = int(kwargs["damage"])
    matches: list[bytes] = []
    for phase in range(13):
        decoded = _decode_v576_body(payload, phase)
        if (
            marker + 40 <= len(decoded)
            and decoded[marker : marker + 3] == b"\x08\x73\x00"
            and int.from_bytes(decoded[marker + 8 : marker + 12], "little") == owner
            and int.from_bytes(decoded[marker + 13 : marker + 17], "little") == obj
            and int.from_bytes(decoded[marker + 19 : marker + 27], "little") == damage
        ):
            matches.append(decoded)
    return matches[0] if len(matches) == 1 else None


def audit_capture(path: Path) -> dict[str, Any]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    ledger = source._decoder.damage_object_origins
    original = ledger.attribute_direct37
    rows: list[dict[str, Any]] = []

    def wrapped(**kwargs: Any):
        result = original(**kwargs)
        if (
            result is None
            or kwargs.get("_evidence_only", False)
            or kwargs.get("_post_admitted_late", False)
            or not result.deterministically_validated
            or int(result.skill_id) not in SKILLS
            or int(result.projectile_template_id) not in TEMPLATES
        ):
            return result
        decoded = _decoded_record(
            kwargs, int(result.owner_actor_id), int(result.damage_object_id)
        )
        if decoded is None:
            return result
        marker = int(kwargs.get("record_offset", 0))
        end = min(len(decoded), marker + 122)
        record = decoded[marker:end]
        rows.append(
            {
                "skill": int(result.skill_id),
                "template": int(result.projectile_template_id),
                "layout": str(kwargs.get("layout", "")),
                "payload_length": len(kwargs["payload"]),
                "marker": marker,
                "record_hex": record.hex(),
            }
        )
        return result

    ledger.attribute_direct37 = wrapped
    try:
        source.poll()
    finally:
        source.close()
    return {"capture": str(path), "rows": rows}


def summarize(captures: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [row for capture in captures for row in capture["rows"]]
    counts = Counter((r["skill"], r["template"]) for r in rows)
    byte_values: dict[int, dict[int, Counter[int]]] = defaultdict(
        lambda: defaultdict(Counter)
    )
    u32_values: dict[int, dict[int, Counter[int]]] = defaultdict(
        lambda: defaultdict(Counter)
    )
    for row in rows:
        skill = int(row["skill"])
        raw = bytes.fromhex(row["record_hex"])
        for offset, value in enumerate(raw[:48]):
            byte_values[offset][skill][value] += 1
        for offset in range(0, min(48, len(raw) - 3)):
            u32_values[offset][skill][int.from_bytes(raw[offset : offset + 4], "little")] += 1

    # Dynamic identity/value fields cannot serve as skill discriminators.
    dynamic_byte_offsets = set(range(4, 8)) | set(range(8, 12)) | set(range(13, 17))
    dynamic_byte_offsets |= set(range(19, 27)) | {35}
    disjoint_bytes = []
    for offset, by_skill in sorted(byte_values.items()):
        if offset in dynamic_byte_offsets or set(by_skill) != set(SKILLS):
            continue
        left = set(by_skill[2207])
        right = set(by_skill[2353])
        if left.isdisjoint(right):
            disjoint_bytes.append(
                {
                    "offset": offset,
                    "firebird": dict(by_skill[2207]),
                    "icicle_injection": dict(by_skill[2353]),
                }
            )

    disjoint_u32 = []
    for offset, by_skill in sorted(u32_values.items()):
        covered = set(range(offset, offset + 4))
        if covered & dynamic_byte_offsets or set(by_skill) != set(SKILLS):
            continue
        left = set(by_skill[2207])
        right = set(by_skill[2353])
        if left.isdisjoint(right):
            disjoint_u32.append(
                {
                    "offset": offset,
                    "firebird_unique_values": len(left),
                    "icicle_injection_unique_values": len(right),
                    "firebird_top": by_skill[2207].most_common(8),
                    "icicle_injection_top": by_skill[2353].most_common(8),
                }
            )

    return {
        "counts": [
            {
                "skill": skill,
                "skill_name": SKILLS[skill],
                "template": template,
                "hits": count,
            }
            for (skill, template), count in sorted(counts.items())
        ],
        "disjoint_fixed_byte_candidates": disjoint_bytes,
        "disjoint_fixed_u32_candidates": disjoint_u32,
        "note": (
            "Candidates exclude result-group, source, object, damage, and critical flag. "
            "A disjoint value in this finite corpus is audit evidence, not automatically "
            "a production rule."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    captures = [audit_capture(path) for path in args.captures]
    result = {
        "schema": "icicle-firebird-hit-attributes-v116",
        "captures": captures,
        "summary": summarize(captures),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(args.output)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
