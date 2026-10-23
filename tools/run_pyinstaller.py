"""Run PyInstaller without relying on the occasionally stalled Windows WMI query."""

from __future__ import annotations

import platform
import os
from pathlib import Path
import sys


if sys.platform == "win32":
    # PyInstaller only uses this probe to distinguish Windows 10/11.  On a few
    # Windows installations platform.win32_ver() can wait forever in WMI.
    platform.win32_ver = lambda *args, **kwargs: ("11", "10.0", "", "")
    platform.system = lambda: "Windows"
    platform.machine = lambda: os.environ.get("PROCESSOR_ARCHITECTURE", "AMD64")
    platform.architecture = lambda *args, **kwargs: ("64bit", "WindowsPE")
    platform.uname = lambda: platform.uname_result("Windows", "", "10", "10.0", "AMD64", "AMD64")
    platform.platform = lambda *args, **kwargs: "Windows-11-10.0-AMD64"

bootstrap = str(Path(__file__).resolve().parent / "pyinstaller_bootstrap")
existing_pythonpath = os.environ.get("PYTHONPATH", "")
os.environ["PYTHONPATH"] = bootstrap + (os.pathsep + existing_pythonpath if existing_pythonpath else "")

from PyInstaller.__main__ import run


if __name__ == "__main__":
    run()
