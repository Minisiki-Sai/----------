"""Check whether broad-suite failures execute this change's runtime paths.

This is NOT a checkout-baseline comparison and must not be labelled one.
"""
import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "outputs/verification/history_catchup_20261020"
nodes = [
    "tests/test_live_dps.py::" + case.get("name")
    for case in ET.parse(OUT / "tests.xml").iter("testcase")
    if case.find("failure") is not None or case.find("error") is not None
]
tracked = {
    "collector.py": {"LiveDpsCollector.__init__", "LiveDpsCollector.poll",
                     "LiveDpsCollector.read_events", "LiveDpsCollector.apply_events",
                     "LiveDpsCollector.archive_events", "LiveDpsCollector.finish_history",
                     "LiveDpsCollector.close"},
    "history.py": {"LiveDpsHistoryStore.start_session"},
    "capture_sources.py": {"V576NpcapSource.poll", "V576NpcapSource.decoded_event_backlog"},
    "live_capture_service.py": {"NativeLiveCapture._run", "NativeLiveCapture.start",
                               "NativeLiveCapture.reset", "NativeLiveCapture._overlay_projection_loop",
                               "NativeLiveCapture._finish_released_history"},
}
calls = Counter()


def trace(frame, event, arg):
    if event == "call":
        code = frame.f_code
        file = code.co_filename.replace("\\", "/").rsplit("/", 1)[-1]
        if (file in {"history_writer.py", "catch_up.py"} and code.co_name != "<module>"
                or code.co_qualname in tracked.get(file, ())):
            calls[f"{file}:{code.co_qualname}"] += 1


if __name__ == "__main__":
    import pytest
    # Import first: class definitions are not runtime persistence/ingest calls.
    import dragon_nest_builder.live_dps.collector
    import dragon_nest_builder.native.live_capture_service
    sys.setprofile(trace)
    try:
        result = pytest.main([*nodes, "-q", "--tb=no",
                              f"--junitxml={OUT / 'failure_scope_tests.xml'}"])
    finally:
        sys.setprofile(None)
    (OUT / "failure_scope.json").write_text(json.dumps(
        {"scope": "Runtime-path trace, not an old-checkout baseline",
         "selected_failures": len(nodes), "pytest_exit_code": int(result),
         "changed_path_calls": dict(calls)}, ensure_ascii=False, indent=2,
    ), encoding="utf-8")
    print("CHANGED_PATH_CALLS", dict(calls))
