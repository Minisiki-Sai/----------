from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "龙之谷模拟器v3.40"
CURRENT = ROOT / "龙之谷模拟器4.01"
SNAPSHOT_SCRIPT = CURRENT / "tools" / "core_snapshot.py"


def snapshot(project: Path) -> dict[str, object]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, str(SNAPSHOT_SCRIPT), "--project", str(project)],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    return json.loads(result.stdout)


def main() -> None:
    baseline = snapshot(BASELINE)
    current = snapshot(CURRENT)
    if baseline != current:
        print("v3.40 与 V4.01 核心计算快照不一致。")
        raise SystemExit(1)
    print("v3.40 与 V4.01 核心计算快照一致。")


if __name__ == "__main__":
    main()
