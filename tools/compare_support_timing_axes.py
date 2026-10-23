from __future__ import annotations

import argparse
import copy
import csv
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.v410_native_core import NativeSimulatorCore


class _DisabledDiskCache:
    def load(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def save(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def discard(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _clear_score_state(core: NativeSimulatorCore, source_hash: str) -> None:
    core.score_theory_planner.invalidate_score_cache()
    core._score_keys_by_replay.pop(source_hash, None)
    core._score_results.clear()
    core._score_benchmark_nests.clear()


def _score_once(
    core: NativeSimulatorCore,
    capture: Path,
    source_hash: str,
    mode: str,
) -> tuple[list[dict[str, Any]], float, dict[str, Any]]:
    _clear_score_state(core, source_hash)
    started = time.perf_counter()
    progress_rows: list[tuple[int, str]] = []
    result = core.score_replay(
        {
            "sourceHash": source_hash,
            "sourceFile": str(capture),
            "bossPreset": "红龙佩达",
        },
        lambda value, message: progress_rows.append((int(value), str(message))),
    )
    elapsed = time.perf_counter() - started
    score_key = str(result.get("scoreKey") or "")
    rows = [asdict(row) for row in core._score_results.get(score_key, ())]
    if not rows:
        raise RuntimeError(f"{mode} 未生成评分结果：{capture.name}")
    return rows, elapsed, {
        "planner": result.get("theoryPlanner"),
        "planner_error": result.get("theoryPlannerError"),
        "diagnostics": copy.deepcopy(result.get("theoryDiagnostics", {})),
        "progress_tail": progress_rows[-20:],
    }


def _skill_rows(row: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (
            str(detail.get("skill_name") or ""),
            str(detail.get("effect_key") or ""),
        ): detail
        for detail in row.get("support_details", ())
        if isinstance(detail, dict) and bool(detail.get("included_in_score", True))
    }


def _rounded(value: Any, digits: int = 6) -> float:
    try:
        return round(float(value or 0.0), digits)
    except (TypeError, ValueError):
        return 0.0


def _compare_rows(
    old_rows: list[dict[str, Any]],
    new_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    old_by_source = {str(row.get("source_id") or ""): row for row in old_rows}
    comparisons: list[dict[str, Any]] = []
    for new in new_rows:
        source_id = str(new.get("source_id") or "")
        old = old_by_source.get(source_id)
        if old is None:
            continue
        old_details = _skill_rows(old)
        new_details = _skill_rows(new)
        detail_rows: list[dict[str, Any]] = []
        for key in sorted(set(old_details) | set(new_details)):
            old_detail = old_details.get(key, {})
            new_detail = new_details.get(key, {})
            old_actual = _rounded(old_detail.get("actual_contribution"))
            new_actual = _rounded(new_detail.get("actual_contribution"))
            old_theory = _rounded(old_detail.get("theory_contribution"))
            new_theory = _rounded(new_detail.get("theory_contribution"))
            old_completion = _rounded(
                old_detail.get(
                    "support_benefit_completion",
                    old_detail.get("completion"),
                )
            )
            new_completion = _rounded(
                new_detail.get(
                    "support_benefit_completion",
                    new_detail.get("completion"),
                )
            )
            detail_rows.append(
                {
                    "skill_name": key[0],
                    "effect_key": key[1],
                    "old_actual_gain": old_actual,
                    "new_actual_gain": new_actual,
                    "actual_gain_delta": _rounded(new_actual - old_actual),
                    "old_theory_gain": old_theory,
                    "new_theory_gain": new_theory,
                    "theory_gain_delta": _rounded(new_theory - old_theory),
                    "old_completion": old_completion,
                    "new_completion": new_completion,
                    "completion_delta": _rounded(new_completion - old_completion),
                }
            )
        old_actual_gain = _rounded(
            sum(_rounded(row.get("actual_contribution")) for row in old_details.values())
        )
        new_actual_gain = _rounded(
            sum(_rounded(row.get("actual_contribution")) for row in new_details.values())
        )
        old_theory_gain = _rounded(
            sum(_rounded(row.get("theory_contribution")) for row in old_details.values())
        )
        new_theory_gain = _rounded(
            sum(_rounded(row.get("theory_contribution")) for row in new_details.values())
        )
        comparisons.append(
            {
                "source_id": source_id,
                "name": str(new.get("name") or old.get("name") or ""),
                "job": str(new.get("job") or old.get("job") or ""),
                # Detail contributions are the score engine's aggregate A-N
                # and T-N values. PlayerScore.support_contribution is instead
                # the final weighted point contribution to total score.
                "old_actual_gain": old_actual_gain,
                "new_actual_gain": new_actual_gain,
                "actual_gain_delta": _rounded(
                    new_actual_gain - old_actual_gain
                ),
                "old_theory_gain": old_theory_gain,
                "new_theory_gain": new_theory_gain,
                "theory_gain_delta": _rounded(
                    new_theory_gain - old_theory_gain
                ),
                "old_support_score": _rounded(old.get("support_score")),
                "new_support_score": _rounded(new.get("support_score")),
                "support_score_delta": _rounded(
                    _rounded(new.get("support_score"))
                    - _rounded(old.get("support_score"))
                ),
                "old_support_weight": _rounded(old.get("support_weight")),
                "new_support_weight": _rounded(new.get("support_weight")),
                "support_weight_delta": _rounded(
                    _rounded(new.get("support_weight"))
                    - _rounded(old.get("support_weight"))
                ),
                "old_support_points": _rounded(old.get("support_contribution")),
                "new_support_points": _rounded(new.get("support_contribution")),
                "support_points_delta": _rounded(
                    _rounded(new.get("support_contribution"))
                    - _rounded(old.get("support_contribution"))
                ),
                "old_total_score": _rounded(old.get("total_score")),
                "new_total_score": _rounded(new.get("total_score")),
                "total_score_delta": _rounded(
                    _rounded(new.get("total_score"))
                    - _rounded(old.get("total_score"))
                ),
                "support_details": detail_rows,
            }
        )
    return comparisons


def _score_fresh_core(
    capture: Path,
    objective_mode: str,
) -> tuple[list[dict[str, Any]], float, float, str, dict[str, Any]]:
    store = DataStore.load()
    core = NativeSimulatorCore(store, BuildCalculator(store))
    core._queue_replay_score_sync = lambda *_args, **_kwargs: None
    core._team_score_disk_cache = _DisabledDiskCache()
    try:
        replay_started = time.perf_counter()
        replay = core.load_replay(str(capture), lambda *_args: None)
        replay_seconds = time.perf_counter() - replay_started
        source_hash = str(replay["sourceHash"])
        core.score_theory_planner._team_score_support_timing_objective_mode = (
            objective_mode
        )
        rows, score_seconds, score_metadata = _score_once(
            core, capture, source_hash, objective_mode
        )
        return (
            rows,
            score_seconds,
            replay_seconds,
            source_hash,
            {
                **copy.deepcopy(
                    getattr(
                        core.score_theory_planner,
                        "_team_score_theory_diagnostics",
                        {},
                    )
                ),
                "_score_metadata": score_metadata,
                "_theory_error": str(
                    getattr(
                        core.score_theory_planner,
                        "_team_score_theory_error",
                        "",
                    )
                    or ""
                ),
                "_support_replans": {
                    str(source_id): copy.deepcopy(
                        plan.get("_support_counterfactual_replan", {})
                    )
                    for source_id, plan in getattr(
                        core.score_theory_planner,
                        "_team_score_theory_plans",
                        {},
                    ).items()
                    if isinstance(plan, dict)
                },
            },
        )
    finally:
        if hasattr(
            core.score_theory_planner,
            "_team_score_support_timing_objective_mode",
        ):
            delattr(
                core.score_theory_planner,
                "_team_score_support_timing_objective_mode",
            )
        core.shutdown()


def compare_capture(
    capture: Path,
) -> dict[str, Any]:
    (
        old_rows,
        old_seconds,
        old_replay_seconds,
        source_hash,
        old_diagnostics,
    ) = _score_fresh_core(
        capture,
        "captured",
    )
    (
        new_rows,
        new_seconds,
        new_replay_seconds,
        new_source_hash,
        new_diagnostics,
    ) = _score_fresh_core(
        capture,
        "neutral_theory",
    )
    if source_hash != new_source_hash:
        raise RuntimeError(f"两次载入产生了不同 sourceHash：{capture.name}")
    rows = _compare_rows(old_rows, new_rows)
    max_actual_gain_delta = max(
        (abs(_rounded(row.get("actual_gain_delta"))) for row in rows),
        default=0.0,
    )
    if max_actual_gain_delta > 1e-5:
        raise RuntimeError(
            f"A-N invariant failed for {capture.name}: "
            f"max delta={max_actual_gain_delta:.6f}"
        )
    return {
        "capture": str(capture),
        "source_hash": source_hash,
        "replay_seconds": _rounded(
            (old_replay_seconds + new_replay_seconds) / 2.0,
            3,
        ),
        "old_score_seconds": _rounded(old_seconds, 3),
        "new_score_seconds": _rounded(new_seconds, 3),
        "old_timing_diagnostics": {
            "score_metadata": old_diagnostics.get("_score_metadata", {}),
            "theory_error": old_diagnostics.get("_theory_error", ""),
            "selected_candidate": old_diagnostics.get(
                "selected_support_timing_candidate"
            ),
            "candidate_team_dps": old_diagnostics.get(
                "support_timing_candidate_team_dps", {}
            ),
            "support_replans": old_diagnostics.get("_support_replans", {}),
        },
        "new_timing_diagnostics": {
            "score_metadata": new_diagnostics.get("_score_metadata", {}),
            "theory_error": new_diagnostics.get("_theory_error", ""),
            "selected_candidate": new_diagnostics.get(
                "selected_support_timing_candidate"
            ),
            "candidate_team_dps": new_diagnostics.get(
                "support_timing_candidate_team_dps", {}
            ),
            "support_replans": new_diagnostics.get("_support_replans", {}),
        },
        "summary": {
            "actual_gain_invariant": True,
            "max_actual_gain_delta": max_actual_gain_delta,
            "old_actual_gain": _rounded(
                sum(_rounded(row.get("old_actual_gain")) for row in rows)
            ),
            "new_actual_gain": _rounded(
                sum(_rounded(row.get("new_actual_gain")) for row in rows)
            ),
            "old_theory_gain": _rounded(
                sum(_rounded(row.get("old_theory_gain")) for row in rows)
            ),
            "new_theory_gain": _rounded(
                sum(_rounded(row.get("new_theory_gain")) for row in rows)
            ),
        },
        "rows": rows,
    }


def _write_csv(path: Path, reports: list[dict[str, Any]]) -> None:
    fields = (
        "capture",
        "source_id",
        "name",
        "job",
        "old_actual_gain",
        "new_actual_gain",
        "actual_gain_delta",
        "old_theory_gain",
        "new_theory_gain",
        "theory_gain_delta",
        "old_support_score",
        "new_support_score",
        "support_score_delta",
        "old_support_weight",
        "new_support_weight",
        "support_weight_delta",
        "old_support_points",
        "new_support_points",
        "support_points_delta",
        "old_total_score",
        "new_total_score",
        "total_score_delta",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for report in reports:
            for row in report["rows"]:
                writer.writerow(
                    {
                        "capture": Path(report["capture"]).name,
                        **{field: row.get(field, "") for field in fields[1:]},
                    }
                )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare captured-timing and neutral-theory support ceilings."
    )
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--csv", type=Path)
    args = parser.parse_args()

    reports: list[dict[str, Any]] = []
    for index, capture in enumerate(args.captures, 1):
        resolved = capture.resolve()
        print(f"[{index}/{len(args.captures)}] {resolved.name}", flush=True)
        report = compare_capture(resolved)
        reports.append(report)
        print(
            f"  old={report['old_score_seconds']:.3f}s "
            f"new={report['new_score_seconds']:.3f}s",
            flush=True,
        )

    payload = {
        "comparison": {
            "old": "T optimized against captured damage timing",
            "new": "T optimized against neutral theoretical party damage timing",
            "actual_gain": "A-N",
            "theory_gain": "T-N",
        },
        "captures": reports,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if args.csv:
        _write_csv(args.csv, reports)
    print(f"WROTE {args.output.resolve()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
