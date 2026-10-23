"""Build-only Windows platform probes for PyInstaller child processes."""

from __future__ import annotations

import os
import platform
import sys


if sys.platform == "win32":
    platform.win32_ver = lambda *args, **kwargs: ("11", "10.0", "", "")
    platform.system = lambda: "Windows"
    platform.machine = lambda: os.environ.get("PROCESSOR_ARCHITECTURE", "AMD64")
    platform.architecture = lambda *args, **kwargs: ("64bit", "WindowsPE")
    platform.uname = lambda: platform.uname_result("Windows", "", "10", "10.0", "AMD64", "AMD64")
    platform.platform = lambda *args, **kwargs: "Windows-11-10.0-AMD64"
