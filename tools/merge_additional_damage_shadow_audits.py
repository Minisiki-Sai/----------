from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.live_dps.additional_damage_shadow import SHADOW_EFFECT_RULES
from tools.audit_additional_damage_shadow import (
    _write_csv,
    _write_markdown,
    effect_audit_summary,
    effect_candidate_inventory,
    job_candidate_coverage,
)


def _capture_name(value: object) -> str:
    return Path(str(value or "")).name


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Merge shadow-audit JSON files; later inputs replace the same capture."
    )
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    captures: dict[str, dict[str, object]] = {}
    details: dict[str, list[dict[str, object]]] = {}
    summaries: dict[str, list[dict[str, object]]] = {}
    for path in args.inputs:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload.get("captures", []):
            name = _capture_name(row.get("capture"))
            if name:
                captures[name] = row
        grouped_details: dict[str, list[dict[str, object]]] = {}
        for row in payload.get("details", []):
            grouped_details.setdefault(_capture_name(row.get("capture")), []).append(row)
        grouped_summaries: dict[str, list[dict[str, object]]] = {}
        for row in payload.get("summary", []):
            grouped_summaries.setdefault(_capture_name(row.get("capture")), []).append(row)
        details.update(grouped_details)
        summaries.update(grouped_summaries)

    ordered_names = list(captures)
    all_details = [row for name in ordered_names for row in details.get(name, [])]
    all_summary = [row for name in ordered_names for row in summaries.get(name, [])]
    candidates = effect_candidate_inventory()
    effect_audit = effect_audit_summary(all_details, candidates)
    result = {
        "format": "additional-damage-shadow-audit-v3",
        "productionAttributionChanged": False,
        "rules": [asdict(rule) for rule in SHADOW_EFFECT_RULES],
        "captures": [captures[name] for name in ordered_names],
        "summary": all_summary,
        "details": all_details,
        "effectCandidates": candidates,
        "effectAudit": effect_audit,
        "jobCoverage": job_candidate_coverage(candidates),
        "statusTotals": dict(Counter(str(row["auditStatus"]) for row in all_details)),
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    stem = args.output.with_suffix("")
    _write_csv(stem.with_name(stem.name + "_summary.csv"), all_summary)
    _write_csv(stem.with_name(stem.name + "_details.csv"), all_details)
    _write_csv(stem.with_name(stem.name + "_job_coverage.csv"), result["jobCoverage"])
    _write_csv(stem.with_name(stem.name + "_effect_audit.csv"), effect_audit)
    _write_markdown(stem.with_suffix(".md"), result)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "captures": len(captures),
                "details": len(all_details),
                "statusTotals": result["statusTotals"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
