from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.audit_cast_damage_tokens import (  # noqa: E402
    WireRow,
    _latest_matching_action,
    audit_capture,
)


WIDTHS = (2, 3, 4)
MAX_ACTION_AGE_SECONDS = 12.0


def _shape(row: WireRow) -> str:
    return f"{row.signature}:{row.payload_length}:{row.layout}"


def _token_fields(
    row: WireRow,
    *,
    width: int,
    before: int,
    after: int,
) -> dict[int, int]:
    start = max(0, row.marker_offset - before)
    stop = min(len(row.body) - width + 1, row.marker_offset + after)
    excluded = {
        0,
        1,
        (1 << (width * 8)) - 1,
        row.source_actor_id & ((1 << (width * 8)) - 1),
        row.skill_id & ((1 << (width * 8)) - 1),
        row.assigned_skill_id & ((1 << (width * 8)) - 1),
    }
    result: dict[int, int] = {}
    for offset in range(start, stop):
        value = int.from_bytes(row.body[offset : offset + width], "little")
        if value in excluded:
            continue
        # One-byte constants shifted into a wider unaligned window create a
        # huge number of false candidates.  Require at least two meaningful
        # bytes while still allowing the observed 16-bit runtime counters.
        if value <= 0xFF:
            continue
        result[offset - row.marker_offset] = value
    return result


def _candidate_rows(
    results: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[tuple[Any, ...], Counter[str]]]:
    support: Counter[tuple[Any, ...]] = Counter()
    values: dict[tuple[Any, ...], set[int]] = defaultdict(set)
    skills: dict[tuple[Any, ...], Counter[int]] = defaultdict(Counter)
    captures: dict[tuple[Any, ...], set[str]] = defaultdict(set)

    for result in results:
        actions = sorted(result["actions"], key=lambda row: row.timestamp)
        for damage in result["exact_damage"]:
            action = _latest_matching_action(actions, damage)
            if action is None:
                continue
            for width in WIDTHS:
                action_fields = _token_fields(
                    action, width=width, before=8, after=96
                )
                damage_fields = _token_fields(
                    damage, width=width, before=8, after=220
                )
                action_by_value: dict[int, list[int]] = defaultdict(list)
                for relative, value in action_fields.items():
                    action_by_value[value].append(relative)
                for damage_relative, value in damage_fields.items():
                    for action_relative in action_by_value.get(value, ()):
                        key = (
                            width,
                            action.layout,
                            _shape(damage),
                            action_relative,
                            damage_relative,
                        )
                        support[key] += 1
                        values[key].add(value)
                        skills[key][damage.skill_id] += 1
                        captures[key].add(damage.capture)

    rendered: list[dict[str, Any]] = []
    stats: dict[tuple[Any, ...], Counter[str]] = {}
    for key, pair_hits in support.items():
        # Discovery alone is not proof.  These modest thresholds merely keep
        # validation tractable; the independent exact-row pass below decides
        # whether a relation is admissible.
        if pair_hits < 8 or len(values[key]) < 4:
            continue
        width, action_layout, damage_shape, action_offset, damage_offset = key
        row = {
            "width": width,
            "action_layout": action_layout,
            "damage_shape": damage_shape,
            "action_relative_offset": action_offset,
            "damage_relative_offset": damage_offset,
            "discovery_pairs": pair_hits,
            "distinct_values": len(values[key]),
            "discovery_captures": len(captures[key]),
            "discovery_skills": dict(skills[key].most_common()),
        }
        rendered.append(row)
        stats[key] = Counter()
    rendered.sort(
        key=lambda row: (
            -int(row["discovery_captures"]),
            -len(row["discovery_skills"]),
            -int(row["distinct_values"]),
            -int(row["discovery_pairs"]),
        )
    )
    # Avoid validating thousands of unaligned variants that encode the same
    # byte run.  The report still retains a broad top set for inspection.
    return rendered[:2000], {
        (
            row["width"],
            row["action_layout"],
            row["damage_shape"],
            row["action_relative_offset"],
            row["damage_relative_offset"],
        ): Counter()
        for row in rendered[:2000]
    }


def _validate(
    candidates: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates_by_damage: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        candidates_by_damage[str(candidate["damage_shape"])].append(candidate)

    for result in results:
        actions: list[WireRow] = sorted(
            result["actions"], key=lambda row: row.timestamp
        )
        rows: list[tuple[WireRow, bool]] = [
            *((row, True) for row in result["exact_damage"]),
            *((row, False) for row in result["nonexact_damage"]),
        ]
        action_indexes: dict[
            tuple[int, str, int, int, int], list[tuple[float, int]]
        ] = defaultdict(list)
        needed_action_fields = {
            (
                int(candidate["width"]),
                str(candidate["action_layout"]),
                int(candidate["action_relative_offset"]),
            )
            for candidate in candidates
        }
        for action in actions:
            for width, layout, relative in needed_action_fields:
                if action.layout != layout:
                    continue
                offset = action.marker_offset + relative
                if offset < 0 or offset + width > len(action.body):
                    continue
                value = int.from_bytes(action.body[offset : offset + width], "little")
                if value <= 0xFF:
                    continue
                action_indexes[
                    (
                        action.source_actor_id,
                        layout,
                        width,
                        relative,
                        value,
                    )
                ].append((action.timestamp, action.skill_id))

        for damage, is_exact in rows:
            for candidate in candidates_by_damage.get(_shape(damage), ()):
                width = int(candidate["width"])
                damage_relative = int(candidate["damage_relative_offset"])
                offset = damage.marker_offset + damage_relative
                if offset < 0 or offset + width > len(damage.body):
                    continue
                value = int.from_bytes(damage.body[offset : offset + width], "little")
                if value <= 0xFF:
                    continue
                action_key = (
                    damage.source_actor_id,
                    str(candidate["action_layout"]),
                    width,
                    int(candidate["action_relative_offset"]),
                    value,
                )
                recent = [
                    (timestamp, skill_id)
                    for timestamp, skill_id in action_indexes.get(action_key, ())
                    if 0.0 <= damage.timestamp - timestamp <= MAX_ACTION_AGE_SECONDS
                ]
                if not recent:
                    continue
                nearest_age = min(damage.timestamp - item[0] for item in recent)
                nearest_skills = {
                    skill_id
                    for timestamp, skill_id in recent
                    if abs((damage.timestamp - timestamp) - nearest_age) <= 0.001
                }
                key_name = "validation_exact" if is_exact else "recovery_nonexact"
                candidate[key_name] = int(candidate.get(key_name, 0)) + 1
                if len(nearest_skills) != 1:
                    candidate[f"{key_name}_ambiguous"] = int(
                        candidate.get(f"{key_name}_ambiguous", 0)
                    ) + 1
                    continue
                predicted = next(iter(nearest_skills))
                if is_exact:
                    verdict = "support" if predicted == damage.skill_id else "conflict"
                    candidate[f"validation_{verdict}"] = int(
                        candidate.get(f"validation_{verdict}", 0)
                    ) + 1
                else:
                    recovered = candidate.setdefault("recovered_skills", {})
                    recovered[str(predicted)] = int(recovered.get(str(predicted), 0)) + 1

    for candidate in candidates:
        support = int(candidate.get("validation_support", 0))
        conflict = int(candidate.get("validation_conflict", 0))
        candidate["validation_precision_percent"] = round(
            support / max(1, support + conflict) * 100.0, 8
        )
        candidate["zero_conflict_candidate"] = (
            support >= 8
            and conflict == 0
            and int(candidate["discovery_captures"]) >= 2
            and len(candidate["discovery_skills"]) >= 2
        )
    candidates.sort(
        key=lambda row: (
            not bool(row["zero_conflict_candidate"]),
            int(row.get("validation_conflict", 0)),
            -int(row.get("validation_support", 0)),
            -int(row.get("recovery_nonexact", 0)),
        )
    )
    return candidates


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit fixed-width runtime tokens shared by exact action and "
            "direct37 rows without treating timing-inferred damage as truth."
        )
    )
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    results = [audit_capture(path.resolve()) for path in args.captures]
    candidates, _stats = _candidate_rows(results)
    validated = _validate(candidates, results)
    result = {
        "schema_version": 1,
        "truth_policy": (
            "Only direct37 fixed-slot skill IDs validate a token. Current "
            "damage assignments are never used as truth."
        ),
        "captures": [str(path.resolve()) for path in args.captures],
        "candidate_count": len(validated),
        "zero_conflict_candidates": sum(
            bool(row["zero_conflict_candidate"]) for row in validated
        ),
        "candidates": validated,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
