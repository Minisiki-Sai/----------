from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _has_runtime(python: Path) -> bool:
    try:
        result = subprocess.run(
            [str(python), "-c", "import PySide6, openpyxl"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _restart_with_project_runtime() -> None:
    try:
        import PySide6  # noqa: F401
        import openpyxl  # noqa: F401
        return
    except ModuleNotFoundError:
        pass
    if os.getenv("DN_HIT_ANALYZER_RELAUNCHED") == "1":
        return
    root = Path(__file__).resolve().parent
    candidates = [root / ".venv" / "Scripts" / "python.exe"]
    launcher = shutil.which("py")
    if launcher:
        for version in ("3.14", "3.13", "3.12", "3.11", "3.10"):
            result = subprocess.run(
                [launcher, f"-{version}", "-c", "import sys; print(sys.executable)"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                candidates.append(Path(result.stdout.strip()))
    for command in ("python", "python3"):
        located = shutil.which(command)
        if located:
            candidates.append(Path(located))
    current = os.path.normcase(str(Path(sys.executable).resolve()))
    seen: set[str] = set()
    for candidate in candidates:
        try:
            candidate = candidate.resolve()
        except OSError:
            continue
        key = os.path.normcase(str(candidate))
        if key in seen or key == current or not candidate.exists():
            continue
        seen.add(key)
        if not _has_runtime(candidate):
            continue
        environment = dict(os.environ)
        environment["DN_HIT_ANALYZER_RELAUNCHED"] = "1"
        completed = subprocess.run(
            [str(candidate), str(Path(__file__).resolve()), *sys.argv[1:]],
            env=environment,
            check=False,
        )
        raise SystemExit(completed.returncode)


_restart_with_project_runtime()

from dragon_nest_builder.hit_analyzer.ui import main


if __name__ == "__main__":
    raise SystemExit(main())
