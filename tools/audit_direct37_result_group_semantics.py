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

from audit_cast_damage_tokens import WireRow, audit_capture  # noqa: E402


def _result_group(row: WireRow) -> int:
    start = row.marker_offset + 4
    return int.from_bytes(row.body[start : start + 4], "little")


def audit(path: Path) -> dict[str, Any]:
    decoded = audit_capture(path)
    exact: list[WireRow] = decoded["exact_damage"]
    nonexact: list[WireRow] = decoded["nonexact_damage"]
    groups: dict[tuple[int, int], list[tuple[bool, WireRow]]] = defaultdict(list)
    for row in exact:
        groups[(row.source_actor_id, _result_group(row))].append((True, row))
    for row in nonexact:
        groups[(row.source_actor_id, _result_group(row))].append((False, row))

    pure = conflicts = recoverable = ambiguous = 0
    pure_spans: list[float] = []
    conflict_spans: list[float] = []
    method_changes: Counter[tuple[str, str]] = Counter()
    recovered_skills: Counter[int] = Counter()
    conflict_examples: list[dict[str, Any]] = []
    long_pure_examples: list[dict[str, Any]] = []

    # A full result-group value is recycled later in long combats.  Its wire
    # meaning is a contiguous server result batch, not a capture-global ID.
    # Reconstruct those per-actor runs without imposing a guessed time window.
    actor_rows: dict[int, list[tuple[bool, WireRow]]] = defaultdict(list)
    for row in exact:
        actor_rows[row.source_actor_id].append((True, row))
    for row in nonexact:
        actor_rows[row.source_actor_id].append((False, row))
    run_pure = run_conflicts = run_recoverable = run_ambiguous = 0
    run_examples: list[dict[str, Any]] = []
    for actor, actor_members in actor_rows.items():
        actor_members.sort(
            key=lambda item: (
                item[1].timestamp,
                0 if item[0] else 1,
                item[1].marker_offset,
            )
        )
        runs: list[list[tuple[bool, WireRow]]] = []
        for member in actor_members:
            if (
                not runs
                or _result_group(runs[-1][-1][1]) != _result_group(member[1])
            ):
                runs.append([member])
            else:
                runs[-1].append(member)
        for run in runs:
            identities = {row.skill_id for is_exact, row in run if is_exact}
            candidates = [row for is_exact, row in run if not is_exact]
            if not identities:
                continue
            if len(identities) != 1:
                run_conflicts += 1
                run_ambiguous += len(candidates)
                continue
            run_pure += 1
            run_recoverable += len(candidates)
            if candidates and len(run_examples) < 40:
                timestamps = [row.timestamp for _, row in run]
                run_examples.append(
                    {
                        "actor": actor,
                        "result_group": _result_group(run[0][1]),
                        "skill": next(iter(identities)),
                        "rows": len(run),
                        "nonexact_rows": len(candidates),
                        "span_ms": round(
                            (max(timestamps) - min(timestamps)) * 1000.0, 3
                        ),
                    }
                )

    for (actor, result_group), members in groups.items():
        identities = {row.skill_id for is_exact, row in members if is_exact}
        if not identities:
            continue
        timestamps = [row.timestamp for _, row in members]
        span_ms = (max(timestamps) - min(timestamps)) * 1000.0
        candidates = [row for is_exact, row in members if not is_exact]
        if len(identities) != 1:
            conflicts += 1
            ambiguous += len(candidates)
            conflict_spans.append(span_ms)
            if len(conflict_examples) < 40:
                conflict_examples.append(
                    {
                        "actor": actor,
                        "result_group": result_group,
                        "span_ms": round(span_ms, 3),
                        "exact_skills": sorted(identities),
                        "exact_rows": [
                            {
                                "timestamp": row.timestamp,
                                "skill": row.skill_id,
                                "layout": row.layout,
                                "signature": row.signature,
                            }
                            for is_exact, row in members
                            if is_exact
                        ][:20],
                        "nonexact_rows": len(candidates),
                    }
                )
            continue
        pure += 1
        pure_spans.append(span_ms)
        skill_id = next(iter(identities))
        recoverable += len(candidates)
        recovered_skills[skill_id] += len(candidates)
        for row in candidates:
            method_changes[(row.assigned_method, str(row.assigned_skill_id))] += 1
        if span_ms > 5.0 and candidates and len(long_pure_examples) < 40:
            long_pure_examples.append(
                {
                    "actor": actor,
                    "result_group": result_group,
                    "span_ms": round(span_ms, 3),
                    "skill": skill_id,
                    "rows": len(members),
                    "nonexact_rows": len(candidates),
                    "first": min(timestamps),
                    "last": max(timestamps),
                }
            )

    def quantile(values: list[float], fraction: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = min(len(ordered) - 1, round((len(ordered) - 1) * fraction))
        return round(ordered[index], 3)

    return {
        "capture": str(path),
        "exact_rows": len(exact),
        "nonexact_rows": len(nonexact),
        "groups": len(groups),
        "pure_anchored_groups": pure,
        "conflicting_anchored_groups": conflicts,
        "recoverable_nonexact": recoverable,
        "ambiguous_nonexact": ambiguous,
        "contiguous_runs": {
            "pure_anchored_runs": run_pure,
            "conflicting_anchored_runs": run_conflicts,
            "recoverable_nonexact": run_recoverable,
            "ambiguous_nonexact": run_ambiguous,
            "examples": run_examples,
        },
        "pure_span_ms": {
            "p50": quantile(pure_spans, 0.5),
            "p90": quantile(pure_spans, 0.9),
            "p99": quantile(pure_spans, 0.99),
            "max": round(max(pure_spans, default=0.0), 3),
        },
        "conflict_span_ms": {
            "p50": quantile(conflict_spans, 0.5),
            "p90": quantile(conflict_spans, 0.9),
            "p99": quantile(conflict_spans, 0.99),
            "max": round(max(conflict_spans, default=0.0), 3),
        },
        "recovered_skills": dict(recovered_skills.most_common()),
        "old_method_skill": [
            {"method": key[0], "old_skill": key[1], "hits": count}
            for key, count in method_changes.most_common(40)
        ],
        "conflict_examples": conflict_examples,
        "long_pure_examples": long_pure_examples,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    reports = [audit(path.resolve()) for path in args.captures]
    totals: Counter[str] = Counter()
    for report in reports:
        for key in (
            "exact_rows",
            "nonexact_rows",
            "groups",
            "pure_anchored_groups",
            "conflicting_anchored_groups",
            "recoverable_nonexact",
            "ambiguous_nonexact",
        ):
            totals[key] += int(report[key])
    result = {
        "schema_version": 1,
        "summary": dict(totals),
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
