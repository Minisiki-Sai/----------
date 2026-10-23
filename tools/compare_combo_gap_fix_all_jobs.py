from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.app import BuildEditor  # noqa: E402
from dragon_nest_builder.calculator import BuildCalculator  # noqa: E402
from dragon_nest_builder.data_loader import DataStore, as_number, norm  # noqa: E402
from tools.benchmark_pure_scheme_1_all_jobs import _run_short  # noqa: E402
from tools.benchmark_timeline_optimizer import _capture_skill_metrics  # noqa: E402


CASES = {
    "burst": {"burst"},
    "burst_cocktail": {"burst", "cocktail"},
}


def timeline_row(editor: BuildEditor, result: dict[str, Any]) -> dict[str, Any]:
    segments = list(result.get("skills", []))
    return {
        "score": as_number(result.get("score")),
        "average": as_number(result.get("average")),
        "window": as_number(result.get("window")),
        "elapsed": as_number(result.get("elapsed")),
        "segments": int(as_number(result.get("segments"))),
        "skills": segments,
        "opening": list(result.get("opening", [])),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the all-job short-axis regression for the immediate-combo fix."
    )
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    app = QApplication.instance() or QApplication([])
    store = DataStore.load()
    calculator = BuildCalculator(store)
    jobs = [
        (first_job, second_job)
        for first_job in store.first_jobs()
        for second_job in store.second_jobs(first_job)
        if norm(second_job)
    ]

    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    started = time.perf_counter()
    for index, (first_job, second_job) in enumerate(jobs, 1):
        editor = None
        try:
            build = copy.deepcopy(store.defaults["build1"])
            build["first_job"] = first_job
            build["second_job"] = second_job
            build["buffs"] = []
            editor = BuildEditor(second_job, store, calculator, build)
            editor._export_skill_sim_page = editor._skill_sim_page()
            metrics = _capture_skill_metrics(editor)
            case_results = {
                case_name: timeline_row(
                    editor,
                    _run_short(editor, metrics, modes, "pure_new"),
                )
                for case_name, modes in CASES.items()
            }
            rows.append(
                {
                    "index": index,
                    "first_job": first_job,
                    "second_job": second_job,
                    "metric_count": len(metrics),
                    "cases": case_results,
                }
            )
            print(
                f"[{index}/{len(jobs)}] {second_job}: "
                f"burst={case_results['burst']['score']:.3f}, "
                f"burst+cocktail={case_results['burst_cocktail']['score']:.3f}",
                flush=True,
            )
        except Exception as exc:
            errors.append(
                {
                    "first_job": first_job,
                    "second_job": second_job,
                    "error": repr(exc),
                }
            )
            print(f"[{index}/{len(jobs)}] {second_job}: ERROR {exc!r}", flush=True)
        finally:
            if editor is not None:
                editor.deleteLater()
                app.processEvents()

    payload = {
        "label": args.label,
        "jobs": len(rows),
        "elapsed": time.perf_counter() - started,
        "cases": list(CASES),
        "rows": rows,
        "errors": errors,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"saved: {args.output}", flush=True)


if __name__ == "__main__":
    main()
