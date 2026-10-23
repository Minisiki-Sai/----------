from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path


REPRESENTATIVE_JOBS = (
    ("剑圣", "剑皇"),
    ("元素", "冰灵"),
    ("贤者", "十字军"),
    ("祭司", "雷神"),
    ("工程", "机械大师"),
    ("光明之怒", "曜"),
)
SUMMARY_FIELDS = (
    "effective_panel",
    "final_percent",
    "crit_percent",
    "crit_damage_percent",
    "min_physical_attack",
    "max_physical_attack",
    "min_magic_attack",
    "max_magic_attack",
)


def rounded(value: object) -> object:
    if isinstance(value, float):
        return round(value, 8)
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    args = parser.parse_args()
    project = args.project.resolve()
    sys.path.insert(0, str(project))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from dragon_nest_builder.calculator import BuildCalculator
    from dragon_nest_builder.data_loader import DataStore

    store = DataStore.load()
    calculator = BuildCalculator(store)
    snapshot: dict[str, object] = {
        "table_sizes": {name: len(rows) for name, rows in sorted(store.tables.items())},
        "first_jobs": store.first_jobs(),
        "builds": {},
    }
    builds: dict[str, object] = snapshot["builds"]  # type: ignore[assignment]
    for first_job, second_job in REPRESENTATIVE_JOBS:
        build = copy.deepcopy(store.defaults["build1"])
        build["first_job"] = first_job
        build["second_job"] = second_job
        rows, summary = calculator.calculate(build)
        builds[second_job] = {
            "summary": {field: rounded(summary.get(field, 0.0)) for field in SUMMARY_FIELDS},
            "rows": {
                row.attr: [rounded(row.converted), rounded(row.buffed)]
                for row in rows
            },
        }
    print(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
