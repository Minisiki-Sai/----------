from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = (
    ROOT / "outputs" / "hit_analyzer_v3_corpus" / "hit_corpus_report.json"
)
DEFAULT_OUTPUT = (
    ROOT
    / "dragon_nest_builder"
    / "data"
    / "live_dps_profiles"
    / "timeline_cast_evidence_v1.json"
)


def _number(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _integer(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _summary_number(value: object, key: str, default: float = 0.0) -> float:
    if isinstance(value, dict):
        return _number(value.get(key), default)
    return _number(value, default)


def _profile_key(row: dict[str, Any]) -> str:
    return "|".join(
        (
            str(row.get("job") or "").strip(),
            str(row.get("skill_name") or "").strip(),
            str(row.get("skill_id") or "").strip(),
        )
    )


def _compact_profile(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "job": str(row.get("job") or "").strip(),
        "skill_name": str(row.get("skill_name") or "").strip(),
        "skill_family": str(row.get("skill_family") or "").strip(),
        "skill_id": str(row.get("skill_id") or "").strip(),
        "analysis_mode": str(row.get("analysis_mode") or "").strip(),
        "sample_count": _integer(row.get("sample_count")),
        "training_sample_count": _integer(row.get("training_sample_count")),
        "file_count": _integer(row.get("file_count")),
        "actor_count": _integer(row.get("actor_count")),
        "reliable_cast_ratio": round(_number(row.get("reliable_cast_ratio")), 6),
        "strong_cast_ratio": round(_number(row.get("strong_cast_ratio")), 6),
        "target_consistency": round(_number(row.get("target_consistency")), 6),
        "cross_file_consistency": round(
            _number(row.get("cross_file_consistency")), 6
        ),
        "balanced_top_share": round(_number(row.get("balanced_top_share")), 6),
        "balanced_margin": round(_number(row.get("balanced_margin")), 6),
        "hit_count_p10": round(_number(row.get("hit_count_p10")), 4),
        "hit_count_median": round(_number(row.get("hit_count_median")), 4),
        "hit_count_p90": round(_number(row.get("hit_count_p90")), 4),
        "first_hit_seconds": round(
            _summary_number(row.get("first_hit_seconds"), "median"), 4
        ),
        "last_hit_seconds": round(
            _summary_number(row.get("last_hit_seconds"), "median"), 4
        ),
        "span_seconds": round(
            _summary_number(row.get("hit_span_seconds"), "median"), 4
        ),
        "status": str(row.get("status") or "").strip(),
    }


def build_payload(report: dict[str, Any]) -> dict[str, Any]:
    profiles: dict[str, dict[str, Any]] = {}
    for row in report.get("skills", []):
        if not isinstance(row, dict):
            continue
        key = _profile_key(row)
        if key == "||":
            continue
        compact = _compact_profile(row)
        entry = profiles.setdefault(
            key,
            {
                "job": compact["job"],
                "skill_name": compact["skill_name"],
                "skill_family": compact["skill_family"],
                "skill_id": compact["skill_id"],
                "fixed": [],
                "persistent": [],
            },
        )
        target = (
            "persistent"
            if compact["analysis_mode"] == "persistent_cadence"
            else "fixed"
        )
        entry[target].append(compact)

    for entry in profiles.values():
        entry["fixed"].sort(
            key=lambda row: (
                -row["training_sample_count"],
                -row["file_count"],
                -row["sample_count"],
            )
        )
        entry["persistent"].sort(
            key=lambda row: (-row["file_count"], -row["sample_count"])
        )
        best = entry["fixed"][0] if entry["fixed"] else None
        entry["corpus_recovery"] = bool(
            best
            and best["training_sample_count"] >= 15
            and best["file_count"] >= 3
            and best["actor_count"] >= 2
            and best["reliable_cast_ratio"] >= 0.50
            and best["balanced_top_share"] >= 0.67
            and best["balanced_margin"] >= 0.20
            and best["cross_file_consistency"] >= 0.67
            and best["target_consistency"] >= 0.50
        )
        entry["persistent_only"] = bool(
            entry["persistent"] and not entry["fixed"]
        )

    overview = report.get("overview", {})
    return {
        "schema_version": 1,
        "timeline_schema_version": "timeline-evidence-v1",
        "source": {
            "analyzer_version": overview.get("analyzer_version"),
            "analysis_signature": overview.get("analysis_signature"),
            "generated_at": overview.get("generated_at"),
            "captures_analyzed": overview.get("captures_analyzed"),
        },
        "policy": {
            "description": (
                "Corpus evidence only. Entries are observations, not per-skill "
                "allow/deny rules. Runtime recovery also requires factual Hit "
                "evidence and an independent skill link."
            ),
            "minimum_training_samples": 15,
            "minimum_files": 3,
            "minimum_actors": 2,
            "minimum_reliable_cast_ratio": 0.50,
            "minimum_balanced_top_share": 0.67,
            "minimum_balanced_margin": 0.20,
            "minimum_cross_file_consistency": 0.67,
            "minimum_target_consistency": 0.50,
        },
        "profiles": dict(sorted(profiles.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build compact runtime timeline evidence from Hit Analyzer V3."
    )
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    report = json.loads(args.report.read_text(encoding="utf-8"))
    if int(report.get("schema_version") or 0) != 3:
        raise SystemExit("expected a Hit Analyzer V3 report")
    payload = build_payload(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "profiles": len(payload["profiles"]),
                "corpus_recovery": sum(
                    bool(row.get("corpus_recovery"))
                    for row in payload["profiles"].values()
                ),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
