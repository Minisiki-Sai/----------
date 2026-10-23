from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT.parent / "龙之谷模拟器4.01"
WORKER = Path(__file__).with_name("replay_snapshot_parity_worker.py")


def _run(project: Path, capture: Path) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [
            sys.executable,
            str(WORKER),
            "--project",
            str(project),
            "--capture",
            str(capture),
        ],
        cwd=project,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(result.stdout)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--current", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    baseline = _run(args.baseline.resolve(), args.capture.resolve())
    current = _run(args.current.resolve(), args.capture.resolve())
    field_names = sorted(set(baseline["fields"]) | set(current["fields"]))
    differences = {
        name: {
            "baseline": baseline["fields"].get(name),
            "current": current["fields"].get(name),
        }
        for name in field_names
        if baseline["fields"].get(name) != current["fields"].get(name)
    }
    report = {
        "equal": not differences
        and baseline["decodedEventsSha256"] == current["decodedEventsSha256"]
        and baseline["totals"] == current["totals"],
        "capture": str(args.capture.resolve()),
        "baseline": str(args.baseline.resolve()),
        "current": str(args.current.resolve()),
        "eventStreamEqual": baseline["decodedEventsSha256"] == current["decodedEventsSha256"],
        "snapshotEqual": baseline["snapshotSha256"] == current["snapshotSha256"],
        "baselineTotals": baseline["totals"],
        "currentTotals": current["totals"],
        "differentFields": differences,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["equal"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
