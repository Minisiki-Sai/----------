from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_V425 = ROOT.parent / "龙之谷模拟器4.20"
WORKER = Path(__file__).with_name("v430_parity_worker.py")


def _run(
    project: Path,
    capture: Path,
    *,
    boss_preset: str,
    skip_score: bool,
    include_profile_details: bool = False,
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(WORKER),
        "--project",
        str(project),
        "--capture",
        str(capture),
        "--boss-preset",
        boss_preset,
    ]
    if skip_score:
        command.append("--skip-score")
    if include_profile_details:
        command.append("--include-profile-details")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    # Boss target ids are assembled from sets in the unchanged v4.25 code.
    # A fixed hash seed keeps their semantically irrelevant list order stable
    # across the two isolated comparison processes.
    env["PYTHONHASHSEED"] = "0"
    result = subprocess.run(
        command,
        cwd=project,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(result.stdout)


def _different_fields(
    baseline: dict[str, Any], current: dict[str, Any], key: str
) -> dict[str, Any]:
    before = baseline.get(key, {})
    after = current.get(key, {})
    names = sorted(set(before) | set(after))
    return {
        name: {"v4.25": before.get(name), "v4.30": after.get(name)}
        for name in names
        if before.get(name) != after.get(name)
    }


def _recursive_differences(
    before: Any,
    after: Any,
    *,
    path: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def visit(left: Any, right: Any, current: str) -> None:
        if len(rows) >= limit or left == right:
            return
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(set(left) | set(right), key=str):
                visit(left.get(key), right.get(key), f"{current}.{key}" if current else str(key))
                if len(rows) >= limit:
                    return
            return
        if isinstance(left, list) and isinstance(right, list):
            for index in range(max(len(left), len(right))):
                visit(
                    left[index] if index < len(left) else None,
                    right[index] if index < len(right) else None,
                    f"{current}[{index}]",
                )
                if len(rows) >= limit:
                    return
            return
        rows.append({"path": current, "v4.25": left, "v4.30": right})

    visit(before, after, path)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--v425", type=Path, default=DEFAULT_V425)
    parser.add_argument("--v430", type=Path, default=ROOT)
    parser.add_argument("--boss-preset", default="全部 Boss")
    parser.add_argument("--skip-score", action="store_true")
    parser.add_argument("--profile-details", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    rows: list[dict[str, Any]] = []
    for capture in args.captures:
        capture = capture.resolve()
        baseline = _run(
            args.v425.resolve(),
            capture,
            boss_preset=args.boss_preset,
            skip_score=args.skip_score,
            include_profile_details=args.profile_details,
        )
        current = _run(
            args.v430.resolve(),
            capture,
            boss_preset=args.boss_preset,
            skip_score=args.skip_score,
            include_profile_details=args.profile_details,
        )
        comparisons = {
            name: baseline.get(name) == current.get(name)
            for name in (
                "captureSha256",
                "decodedEventCount",
                "decodedEventTypes",
                "decodedEventsSha256",
                "snapshotSha256",
                "replaySha256",
                "overlaySha256",
                "scoreSha256",
            )
        }
        rows.append(
            {
                "capture": str(capture),
                "equal": all(comparisons.values()),
                "comparisons": comparisons,
                "snapshotDifferences": _different_fields(
                    baseline, current, "snapshotFields"
                ),
                "replayDifferences": _different_fields(
                    baseline, current, "replayFields"
                ),
                "scoreDifferences": _different_fields(
                    baseline, current, "scoreFields"
                ),
                "profileDifferences": _recursive_differences(
                    baseline.get("profileDetails", {}),
                    current.get("profileDetails", {}),
                )
                if args.profile_details
                else [],
                "v425OverlaySamples": baseline.get("overlaySamples", []),
                "v430OverlaySamples": current.get("overlaySamples", []),
            }
        )

    report = {"equal": all(row["equal"] for row in rows), "captures": rows}
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["equal"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
