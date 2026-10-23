from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT.parent / "龙之谷模拟器4.01"


def snapshot(project: Path) -> str:
    tool = project / "tools" / "core_snapshot.py"
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, str(tool), "--project", str(project)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    payload = json.loads(result.stdout)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--current", type=Path, default=ROOT)
    args = parser.parse_args()

    baseline = snapshot(args.baseline.resolve())
    current = snapshot(args.current.resolve())
    baseline_hash = hashlib.sha256(baseline.encode("utf-8")).hexdigest()
    current_hash = hashlib.sha256(current.encode("utf-8")).hexdigest()
    report = {
        "baseline": str(args.baseline.resolve()),
        "current": str(args.current.resolve()),
        "equal": baseline == current,
        "baselineSha256": baseline_hash,
        "currentSha256": current_hash,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["equal"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
