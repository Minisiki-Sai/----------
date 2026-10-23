from __future__ import annotations

"""V4.20 source launcher.

The V4.20 folder owns the Vue/Tauri desktop interface and its window-free
Python service. The preserved V4.10 folder remains available for historical
comparison and rollback.
"""

from run_v420 import run_frontend


if __name__ == "__main__":
    raise SystemExit(run_frontend())
