from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


_TOP_LEVEL_HASHES = (
    "captureSha256",
    "decodedEventsSha256",
    "snapshotSha256",
    "replaySha256",
    "overlaySha256",
    "scoreSha256",
    "scoreDetailsSha256",
    "theoryPlansSha256",
)
_OPTIONAL_HASHES = frozenset({"scoreDetailsSha256", "theoryPlansSha256"})


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"parity report must be an object: {path}")
    return value


def _canonical_boss_rows(value: dict[str, Any]) -> dict[str, Any]:
    selected = value.get("replaySelected")
    if not isinstance(selected, dict):
        return {}
    canonical: dict[str, Any] = {}
    for section in ("boss_encounters", "boss_effect_views"):
        rows: list[Any] = []
        for raw_row in selected.get(section, []):
            if not isinstance(raw_row, dict):
                rows.append(raw_row)
                continue
            row = dict(raw_row)
            target_ids = sorted(str(item) for item in row.get("target_ids", []))
            row["target_ids"] = target_ids
            base_stats = row.get("base_stats")
            if isinstance(base_stats, dict):
                base_stats = dict(base_stats)
                template_id = str(base_stats.get("template_id", ""))
                target_templates = {
                    target_id.rsplit("@", 1)[-1]
                    for target_id in target_ids
                    if "@" in target_id
                }
                # The old page projection chooses one member of a grouped
                # Boss target frozenset as representative metadata. Across
                # processes that representative can differ although every
                # numeric Boss stat and the complete target set are equal.
                if template_id and template_id in target_templates:
                    base_stats["template_id"] = "<group-member>"
                row["base_stats"] = base_stats
            rows.append(row)
        canonical[section] = rows
    return canonical


def _business_mismatches(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    mismatches: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    remaining = dict(mismatches)
    known: dict[str, Any] = {}
    if _canonical_boss_rows(baseline) == _canonical_boss_rows(candidate):
        replay_fields = remaining.get("replayFields")
        if isinstance(replay_fields, dict) and set(replay_fields).issubset(
            {"boss_encounters", "boss_effect_views"}
        ):
            known["bossSetSerialization"] = replay_fields
            remaining.pop("replayFields", None)
            replay_hash = remaining.pop("replaySha256", None)
            if replay_hash is not None:
                known["replaySha256"] = replay_hash
    return remaining, known


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    baseline = _load(args.baseline.resolve())
    candidate = _load(args.candidate.resolve())
    mismatches: dict[str, dict[str, Any]] = {}
    for key in _TOP_LEVEL_HASHES:
        if key in _OPTIONAL_HASHES and (
            key not in baseline or key not in candidate
        ):
            continue
        if baseline.get(key) != candidate.get(key):
            mismatches[key] = {
                "baseline": baseline.get(key),
                "candidate": candidate.get(key),
            }
    for section in ("snapshotFields", "replayFields", "scoreFields"):
        baseline_fields = baseline.get(section, {})
        candidate_fields = candidate.get(section, {})
        field_mismatches = {
            key: {
                "baseline": baseline_fields.get(key),
                "candidate": candidate_fields.get(key),
            }
            for key in sorted(set(baseline_fields) | set(candidate_fields))
            if baseline_fields.get(key) != candidate_fields.get(key)
        }
        if field_mismatches:
            mismatches[section] = field_mismatches
    business_mismatches, known_nondeterminism = _business_mismatches(
        baseline, candidate, mismatches
    )
    report = {
        "equal": not mismatches,
        "businessEqual": not business_mismatches,
        "baseline": str(args.baseline.resolve()),
        "candidate": str(args.candidate.resolve()),
        "decodedEventCount": candidate.get("decodedEventCount", 0),
        "eventPlatform": candidate.get("eventPlatform", {}),
        "mismatches": mismatches,
        "businessMismatches": business_mismatches,
        "knownNondeterminism": known_nondeterminism,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if not business_mismatches else 1


if __name__ == "__main__":
    raise SystemExit(main())
