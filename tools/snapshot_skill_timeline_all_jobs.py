from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication

from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore, norm
from tools.benchmark_pure_scheme_1_all_jobs import _run_long, _run_short
from tools.benchmark_timeline_optimizer import _prepare_editor


CASES = {
    "long": None,
    "burst": {"burst"},
    "accel": {"accel"},
}


def _snapshot(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "score": result.get("score", 0.0),
        "average": result.get("average", 0.0),
        "window": result.get("window", 0.0),
        "segments": result.get("segments", 0),
        "elapsed": result.get("elapsed", 0.0),
        "skills": result.get("skills", []),
        "opening": result.get("opening", []),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Snapshot all skill-simulation timelines.")
    parser.add_argument("--window", type=float, default=125.0)
    parser.add_argument("--job", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    requested = {norm(value) for value in args.job.split(",") if norm(value)}
    app = QApplication.instance() or QApplication([])
    store = DataStore.load()
    calculator = BuildCalculator(store)
    pairs = [
        (first, second)
        for first in store.first_jobs()
        for second in store.second_jobs(first)
        if second and (not requested or norm(second) in requested)
    ]
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    started_all = time.perf_counter()
    for index, (first_job, second_job) in enumerate(pairs, 1):
        editor = None
        try:
            editor, metrics = _prepare_editor(app, store, calculator, first_job, second_job)
            cases: dict[str, dict[str, Any]] = {}
            for name, modes in CASES.items():
                result = (
                    _run_long(editor, metrics, args.window, "pure_new")
                    if modes is None
                    else _run_short(editor, metrics, set(modes), "pure_new")
                )
                cases[name] = _snapshot(result)
            rows.append({"first_job": first_job, "second_job": second_job, "cases": cases})
            summary = ", ".join(f"{name}={cases[name]['score']:.3f}" for name in CASES)
            print(f"[{index}/{len(pairs)}] {second_job}: {summary}", flush=True)
        except Exception as exc:
            errors.append({"first_job": first_job, "second_job": second_job, "error": repr(exc)})
            print(f"[{index}/{len(pairs)}] {second_job}: ERROR {exc!r}", flush=True)
        finally:
            if editor is not None:
                editor.deleteLater()
                app.processEvents()

    payload = {
        "window": args.window,
        "elapsed": time.perf_counter() - started_all,
        "rows": rows,
        "errors": errors,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved: {args.output}", flush=True)


if __name__ == "__main__":
    main()
