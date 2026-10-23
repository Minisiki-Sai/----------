from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from v420_backend import DEFAULT_PORT, serve


ROOT = Path(__file__).resolve().parent


def run_frontend() -> int:
    frontend = ROOT / "frontend"
    pnpm = os.environ.get("PNPM_BIN") or shutil.which("pnpm")
    if not pnpm:
        raise RuntimeError("pnpm is not installed or not available in PATH")
    return subprocess.call([pnpm, "dev"], cwd=frontend)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dragon Nest ACT V4.68 launcher")
    parser.add_argument("--backend-only", action="store_true")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    if args.backend_only:
        serve(port=args.port)
        raise SystemExit(0)
    raise SystemExit(run_frontend())
