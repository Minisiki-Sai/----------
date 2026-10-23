from __future__ import annotations

import argparse
import json
import sys
from bisect import bisect_right
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.models import (  # noqa: E402
    CombatEvent,
    SkillCastEvent,
)
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)


WINDOWS_MS = (100, 200, 300, 500, 800, 1_200, 2_000, 3_000, 5_000)
ACTION_KINDS = frozenset(
    {"skill_action", "compact_skill_input", "transformed_basic_attack"}
)


def _action_rows(
    events: list[object],
) -> dict[str, tuple[list[int], list[SkillCastEvent]]]:
    grouped: dict[str, list[SkillCastEvent]] = defaultdict(list)
    for event in events:
        if not isinstance(event, SkillCastEvent):
            continue
        if str(event.packet_kind or "").strip().casefold() not in ACTION_KINDS:
            continue
        grouped[event.source_id].append(event)
    result: dict[str, tuple[list[int], list[SkillCastEvent]]] = {}
    for source_id, rows in grouped.items():
        rows.sort(key=lambda row: row.timestamp_ms)
        result[source_id] = ([row.timestamp_ms for row in rows], rows)
    return result


def _latest_prior_action(
    action_index: tuple[list[int], list[SkillCastEvent]] | None,
    timestamp_ms: int,
    max_age_ms: int,
) -> SkillCastEvent | None:
    if action_index is None:
        return None
    starts, rows = action_index
    index = bisect_right(starts, timestamp_ms) - 1
    if index < 0:
        return None
    action = rows[index]
    age_ms = timestamp_ms - action.timestamp_ms
    if age_ms < 0 or age_ms > max_age_ms:
        return None
    return action


def audit_capture(path: Path) -> dict[str, Any]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    try:
        events = source.poll()
    finally:
        source.close()

    actions = _action_rows(events)
    object_rows: dict[tuple[str, int], list[CombatEvent]] = defaultdict(list)
    for event in events:
        if not isinstance(event, CombatEvent):
            continue
        if event.healing or event.wire_damage_object_id <= 0:
            continue
        object_rows[(event.source_id, event.wire_damage_object_id)].append(event)

    samples: list[dict[str, Any]] = []
    exact_objects = 0
    conflicted_objects = 0
    for (source_id, object_id), rows in object_rows.items():
        exact_identities = {
            (str(row.skill_id), str(row.skill_name))
            for row in rows
            if str(row.attribution_method or "").strip().casefold()
            in {"packet_skill_id", "direct_result_group_link"}
        }
        if not exact_identities:
            continue
        if len(exact_identities) != 1:
            conflicted_objects += 1
            continue
        exact_objects += 1
        exact_skill_id, exact_skill_name = next(iter(exact_identities))
        first = min(rows, key=lambda row: row.timestamp_ms)
        row: dict[str, Any] = {
            "source_id": source_id,
            "object_id": object_id,
            "first_timestamp_ms": first.timestamp_ms,
            "exact_skill_id": exact_skill_id,
            "exact_skill_name": exact_skill_name,
            "hits": len(rows),
        }
        for window_ms in WINDOWS_MS:
            action = _latest_prior_action(
                actions.get(source_id), first.timestamp_ms, window_ms
            )
            if action is None:
                continue
            row[str(window_ms)] = {
                "skill_id": str(action.skill_id),
                "skill_name": str(action.skill_name),
                "timestamp_ms": action.timestamp_ms,
                "age_ms": first.timestamp_ms - action.timestamp_ms,
                "matches": str(action.skill_id) == exact_skill_id,
            }
        samples.append(row)

    windows: dict[str, dict[str, int | float]] = {}
    for window_ms in WINDOWS_MS:
        key = str(window_ms)
        candidates = [row for row in samples if key in row]
        matches = sum(1 for row in candidates if row[key]["matches"])
        mismatches = len(candidates) - matches
        matched_hits = sum(
            int(row["hits"]) for row in candidates if row[key]["matches"]
        )
        mismatch_hits = sum(
            int(row["hits"]) for row in candidates if not row[key]["matches"]
        )
        windows[key] = {
            "candidate_objects": len(candidates),
            "matching_objects": matches,
            "mismatching_objects": mismatches,
            "precision_percent": round(matches / max(1, len(candidates)) * 100, 6),
            "matching_hits": matched_hits,
            "mismatching_hits": mismatch_hits,
        }

    mismatch_examples = []
    widest = str(max(WINDOWS_MS))
    for row in samples:
        if widest not in row or row[widest]["matches"]:
            continue
        mismatch_examples.append(
            {
                "source_id": row["source_id"],
                "object_id": row["object_id"],
                "first_timestamp_ms": row["first_timestamp_ms"],
                "exact_skill_id": row["exact_skill_id"],
                "exact_skill_name": row["exact_skill_name"],
                "candidate": row[widest],
            }
        )
        if len(mismatch_examples) >= 40:
            break

    return {
        "capture": str(path),
        "decoded_events": len(events),
        "damage_objects": len(object_rows),
        "exact_single_skill_objects": exact_objects,
        "conflicted_exact_objects": conflicted_objects,
        "windows": windows,
        "mismatch_examples_at_5000ms": mismatch_examples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate whether a damage object's first direct37 row can be "
            "bound to the latest prior exact action without stealing hits."
        )
    )
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    reports = [audit_capture(path.resolve()) for path in args.captures]
    aggregate: dict[str, Counter[str]] = {
        str(window): Counter() for window in WINDOWS_MS
    }
    for report in reports:
        for window, values in report["windows"].items():
            for name in (
                "candidate_objects",
                "matching_objects",
                "mismatching_objects",
                "matching_hits",
                "mismatching_hits",
            ):
                aggregate[window][name] += int(values[name])
    rendered_aggregate: dict[str, dict[str, int | float]] = {}
    for window, values in aggregate.items():
        candidates = values["candidate_objects"]
        matches = values["matching_objects"]
        rendered_aggregate[window] = {
            **dict(values),
            "precision_percent": round(matches / max(1, candidates) * 100, 6),
        }
    result = {
        "schema_version": 1,
        "policy": (
            "Validation only. A temporal object/action join is not deterministic "
            "unless every exact object anchor agrees and ambiguous overlaps fail closed."
        ),
        "captures": len(reports),
        "aggregate_windows": rendered_aggregate,
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
