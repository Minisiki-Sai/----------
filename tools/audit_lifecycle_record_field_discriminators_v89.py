"""Find record fields that split a shared Projectile into native skill branches.

This is an offline audit.  It uses the same strict owner/object lifecycle
reader as ``audit_unknown_object_lifecycle_variants`` and never treats the
current display skill, damage value, result group, or timing distance as a
label.  Labels come only from exact per-result packet skill IDs.
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.audit_unknown_object_lifecycle_variants import (
    _collect_direct37,
    _scan_s2c_records,
)
from tools.audit_s2c_object_record_table_links import load_client_tables


def _context(record) -> bytes:
    return bytes.fromhex(record.context_hex)


def _field_rows(records, hits, blocking_events, marker: str, template: int):
    by_key = defaultdict(list)
    for row in records:
        if row.marker == marker and row.projectile_template_id == template:
            by_key[row.key].append(row)
    hits_by_key = defaultdict(list)
    for hit in hits:
        hits_by_key[hit.key].append(hit)

    instances = []
    for key, object_rows in by_key.items():
        object_rows.sort(key=lambda row: row.position)
        positions = [row.position for row in object_rows]
        exact_by_record = defaultdict(Counter)
        blockers_by_record = Counter()
        for hit in hits_by_key.get(key, ()):
            at = bisect.bisect_right(positions, hit.position) - 1
            if at >= 0 and hit.packet_skill_id is not None:
                exact_by_record[at][hit.packet_skill_id] += 1
        for (packet_index, owner, object_id), count in blocking_events.items():
            if (owner, object_id) != key:
                continue
            at = bisect.bisect_right(positions, (packet_index, 1 << 30)) - 1
            if at >= 0:
                blockers_by_record[at] += count
        for index, row in enumerate(object_rows):
            raw = _context(row)
            # context starts M-8.  Keep the full post-template state region;
            # owner/object/template and packet position are excluded from the
            # discriminator so a runtime ID cannot become a fake skill key.
            state = raw[24:52] if len(raw) >= 52 else b""
            instances.append(
                {
                    "capture": "",
                    "packet_index": row.packet_index,
                    "marker_offset": row.marker_offset,
                    "owner_actor_id": row.owner_actor_id,
                    "damage_object_id": row.damage_object_id,
                    "exact_skill_ids": {
                        str(skill): count
                        for skill, count in exact_by_record[index].items()
                    },
                    "blocking_hits": int(blockers_by_record[index]),
                    "state_hex_m16_m43": state.hex(),
                    "state_u8_m16_m43": list(state),
                }
            )
    return instances


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resource-root", type=Path, required=True)
    parser.add_argument("--marker", default="09 62")
    parser.add_argument("--template", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("captures", nargs="+")
    args = parser.parse_args()

    tables = load_client_tables(args.resource_root.resolve())
    projectile_ids = frozenset(int(value) for value in tables["projectiles"])
    rows = []
    for capture_text in args.captures:
        capture = Path(capture_text).resolve()
        hits, blockers, _methods, _labels, _all = _collect_direct37(capture)
        records, _rejections, _generic = _scan_s2c_records(
            capture, hits, projectile_ids, scan_generic_layouts=False
        )
        current = _field_rows(
            records, hits, blockers, args.marker, int(args.template)
        )
        for row in current:
            row["capture"] = str(capture)
        rows.extend(current)

    exact = [row for row in rows if len(row["exact_skill_ids"]) == 1]
    skills = sorted(
        {
            int(next(iter(row["exact_skill_ids"])))
            for row in exact
        }
    )
    field_candidates = []
    for offset in range(28):
        values = {}
        for skill in skills:
            values[str(skill)] = sorted(
                {
                    row["state_u8_m16_m43"][offset]
                    for row in exact
                    if int(next(iter(row["exact_skill_ids"]))) == skill
                    and len(row["state_u8_m16_m43"]) == 28
                }
            )
        sets = [set(value) for value in values.values()]
        disjoint = bool(sets) and all(
            sets[left].isdisjoint(sets[right])
            for left in range(len(sets))
            for right in range(left + 1, len(sets))
        )
        if disjoint or len({tuple(value) for value in values.values()}) > 1:
            field_candidates.append(
                {
                    "marker_relative_offset": 16 + offset,
                    "values_by_exact_skill": values,
                    "pairwise_disjoint": disjoint,
                }
            )

    exact_fingerprints = defaultdict(Counter)
    for row in exact:
        skill = next(iter(row["exact_skill_ids"]))
        exact_fingerprints[str(skill)][row["state_hex_m16_m43"]] += 1
    blocking_fingerprints = Counter()
    for row in rows:
        if row["blocking_hits"]:
            blocking_fingerprints[row["state_hex_m16_m43"]] += row["blocking_hits"]

    report = {
        "schema": "lifecycle-record-field-discriminators-v89",
        "policy": {
            "positive_labels": "exact per-result packet skill ID only",
            "forbidden_labels": [
                "current/old display skill",
                "damage amount",
                "result group",
                "nearest action",
            ],
            "production_modified": False,
        },
        "marker": args.marker,
        "projectile_template_id": int(args.template),
        "captures": len(args.captures),
        "instances": len(rows),
        "exact_instances": len(exact),
        "exact_skill_ids": skills,
        "blocking_hits": sum(row["blocking_hits"] for row in rows),
        "field_candidates": field_candidates,
        "exact_state_fingerprints": {
            skill: dict(counter.most_common())
            for skill, counter in exact_fingerprints.items()
        },
        "blocking_state_fingerprints": dict(blocking_fingerprints.most_common()),
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "instances": len(rows),
                "exact_instances": len(exact),
                "blocking_hits": report["blocking_hits"],
                "candidate_fields": len(field_candidates),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
