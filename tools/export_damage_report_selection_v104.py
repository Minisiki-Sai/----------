from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把直接伤害报告导出为旧版只读审计工具可消费的哈希固定语料清单。"
    )
    parser.add_argument("report", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--jobs", nargs="*")
    args = parser.parse_args()

    report = json.loads(args.report.read_text(encoding="utf-8"))
    jobs = frozenset(args.jobs or ())
    selected = []
    for row in report.get("capture_manifest", ()):
        profession_summary = row.get("profession_summary", {})
        if jobs and not any(
            job in profession_summary
            and int(profession_summary[job].get("total_direct_hits", 0)) > 0
            for job in jobs
        ):
            continue
        selected.append(
            {
                "result": "",
                "capture": Path(row["capture_path"]).name,
                "capture_sha256": row["capture_sha256"],
                "result_sha256": "",
            }
        )
    payload = {
        "schema_version": "hash-pinned-audit-selection-v104",
        "sources": {
            "source_report": str(args.report.resolve()),
            "selected_results": selected,
        },
        "summary": {
            "capture_count": len(selected),
            "jobs": sorted(jobs),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload["summary"], ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
