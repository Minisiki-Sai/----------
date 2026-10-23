from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.hit_analyzer.analyzer import ANALYZER_VERSION  # noqa: E402


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def validate(output_dir: Path) -> list[str]:
    report = _load(output_dir / "hit_corpus_report.json")
    candidate = _load(
        output_dir
        / f"skill_hit_templates_v{ANALYZER_VERSION}.candidate.json"
    )
    errors: list[str] = []
    if int(report.get("schema_version", 0) or 0) != ANALYZER_VERSION:
        errors.append(
            f"report schema_version is not {ANALYZER_VERSION}"
        )
    if int(candidate.get("schema_version", 0) or 0) != ANALYZER_VERSION:
        errors.append(
            f"candidate schema_version is not {ANALYZER_VERSION}"
        )
    if report.get("overview", {}).get("analysis_signature") != candidate.get(
        "analysis_signature"
    ):
        errors.append("report and candidate analysis signatures differ")

    distribution_totals: dict[str, float] = defaultdict(float)
    for row in report.get("hit_count_distribution", []):
        if isinstance(row, dict):
            distribution_totals[str(row.get("key", ""))] += float(
                row.get("capture_balanced_share", 0.0) or 0.0
            )
    for key, total in distribution_totals.items():
        if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-9):
            errors.append(f"capture-balanced Hit votes do not sum to 1: {key}={total}")

    position_shares: dict[str, float] = defaultdict(float)
    position_indexes: dict[str, list[int]] = defaultdict(list)
    for row in report.get("positions", []):
        if not isinstance(row, dict):
            continue
        key = str(row.get("key", ""))
        position_shares[key] += float(row.get("share", 0.0) or 0.0)
        position_indexes[key].append(int(row.get("index", 0) or 0))
        p10 = float(row.get("time_p10", 0.0) or 0.0)
        median = float(row.get("time_seconds", 0.0) or 0.0)
        p90 = float(row.get("time_p90", 0.0) or 0.0)
        if median < p10 - 1e-9 or median > p90 + 1e-9:
            errors.append(f"invalid time quantiles: {key} hit {row.get('index')}")
    for key, total in position_shares.items():
        if total and not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-6):
            errors.append(f"component shares do not sum to 1: {key}={total}")
        indexes = sorted(position_indexes[key])
        if indexes != list(range(1, len(indexes) + 1)):
            errors.append(f"non-contiguous Hit positions: {key}")

    quality = report.get("quality_gates", {})
    if isinstance(quality, dict) and quality.get("event_conservation_failures"):
        errors.append("one or more captures failed real-Hit event conservation")

    promoted = candidate.get("promoted_templates", {})
    if promoted:
        errors.append("candidate-only build unexpectedly contains promoted templates")
    for section in ("persistent_cadence", "quarantine", "discovery_only"):
        values = candidate.get(section, {})
        if not isinstance(values, dict):
            errors.append(f"candidate section is not an object: {section}")
            continue
        for key, row in values.items():
            if isinstance(row, dict) and row.get("recommended_hit_count") is not None:
                errors.append(f"non-review section exposes a recommendation: {section}/{key}")
    review = candidate.get("review_candidates", {})
    if isinstance(review, dict):
        for key, row in review.items():
            if not isinstance(row, dict):
                errors.append(f"invalid review candidate: {key}")
                continue
            if row.get("recommended_hit_count") is None:
                errors.append(f"review candidate has no Hit recommendation: {key}")
            if int(row.get("training_sample_count", 0) or 0) < 15:
                errors.append(f"review candidate fails sample gate: {key}")
            if int(row.get("file_count", 0) or 0) < 3:
                errors.append(f"review candidate fails capture gate: {key}")
            if int(row.get("actor_count", 0) or 0) < 2:
                errors.append(f"review candidate fails actor gate: {key}")
            if float(row.get("balanced_top_share", 0.0) or 0.0) < 0.67:
                errors.append(f"review candidate fails top-share gate: {key}")
            if float(row.get("balanced_margin", 0.0) or 0.0) < 0.20:
                errors.append(f"review candidate fails margin gate: {key}")
            if float(row.get("cross_file_consistency", 0.0) or 0.0) < 0.67:
                errors.append(f"review candidate fails cross-file gate: {key}")
            if float(row.get("target_consistency", 0.0) or 0.0) < 0.50:
                errors.append(f"review candidate fails target gate: {key}")

    coverage = report.get("coverage", [])
    if isinstance(coverage, list):
        if any(
            isinstance(row, dict) and str(row.get("skill_name", "")).strip() in {"", "无", "-"}
            for row in coverage
        ):
            errors.append("coverage contains a placeholder skill")
        overview = report.get("overview", {})
        if isinstance(overview, dict):
            covered = sum(
                bool(row.get("has_same_variant_real_hit"))
                for row in coverage
                if isinstance(row, dict)
            )
            trainable = sum(
                bool(row.get("has_same_variant_training_sample"))
                for row in coverage
                if isinstance(row, dict)
            )
            if int(overview.get("configured_damage_skills", -1) or 0) != len(coverage):
                errors.append("configured skill total does not match coverage rows")
            if int(overview.get("configured_skills_with_real_hits", -1) or 0) != covered:
                errors.append("configured real-Hit coverage total is inconsistent")
            if int(overview.get("configured_skills_with_training_samples", -1) or 0) != trainable:
                errors.append("configured training coverage total is inconsistent")
            if int(overview.get("configured_skills_without_samples", -1) or 0) != len(coverage) - covered:
                errors.append("configured missing-skill total is inconsistent")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description=f"Validate V{ANALYZER_VERSION} skill-Hit corpus semantics."
    )
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    errors = validate(args.output.resolve())
    if errors:
        for error in errors:
            print(f"[FAIL] {error}")
        return 1
    print(
        f"[PASS] V{ANALYZER_VERSION} corpus schema, conservation, "
        "distributions and gates are valid."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
