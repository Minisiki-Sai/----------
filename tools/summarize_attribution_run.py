"""Read completed checkpoints without mixing snapshots or unmatched captures.

This is a progress inspector, not a replacement for the replay's final report.
The historical comparison is descriptive: physical corrections can change its
denominator, so it must not be advertised as a pure attribution-only A/B test.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path


def _ratio(numerator: int, denominator: int):
    return round(numerator * 100 / denominator, 4) if denominator else None


def summarize(run_dir: Path, baseline_path: Path, *, project_root: Path | None = None) -> dict:
    project_root = project_root or Path(__file__).resolve().parents[1]
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    manifest = {r["capture_path"]: r for r in baseline["capture_manifest"]}
    if len(manifest) != len(baseline["capture_manifest"]):
        raise ValueError("Historical manifest contains duplicate paths")
    current = defaultdict(Counter)
    old = defaultdict(Counter)
    wide = defaultdict(Counter)
    captures = []
    signature = None
    incomplete_files = []
    seen = set()
    for path in sorted((run_dir / "profession_direct_damage_checkpoints").glob("*.json")):
        try:
            checkpoint = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            incomplete_files.append(path.name)
            continue
        source = checkpoint["source_path"]
        if source in seen:
            raise ValueError(f"Duplicate capture checkpoint: {source}")
        seen.add(source)
        historical = manifest.get(source)
        report = checkpoint["report"]
        if historical is None or historical["capture_sha256"] != report["capture_sha256"]:
            raise ValueError(f"Not the same historical capture bytes: {source}")
        stamp = checkpoint["analysis_signature"]
        if signature is None:
            signature = stamp
        elif stamp != signature:
            raise ValueError("Checkpoint directory contains mixed runtime signatures")
        for job, counts in report["profession_summary"].items():
            current[job].update(counts)
        for job, counts in historical["profession_summary"].items():
            old[job].update(counts)
        for job, counts in report.get("named_direct_candidates", {}).items():
            wide[job].update(counts)
        captures.append({
            "capture": source, "sha256": report["capture_sha256"],
            "raw_invariants_equal": report["raw_source_invariants"] == historical["raw_source_invariants"],
            "aggregate_invariants_equal": report["aggregated_invariants"] == historical["aggregated_invariants"],
        })
    if signature and signature.get("frozen_runtime"):
        frozen = signature["frozen_runtime"]
        for filename, expected in frozen["hashes"].items():
            if hashlib.sha256((Path(frozen["directory"]) / filename).read_bytes()).hexdigest() != expected:
                raise ValueError(f"Frozen runtime was modified: {filename}")
    current_input_changes = []
    if signature:
        frozen = signature.get("frozen_runtime", {})
        modules = frozen.get("modules", {})
        for relative, expected in signature.get("files", {}).items():
            module = relative[:-3].replace("/", ".") if relative.endswith(".py") else ""
            actual_path = (Path(frozen["directory"]) / modules[module]
                           if module in modules else project_root / relative)
            actual = hashlib.sha256(actual_path.read_bytes()).hexdigest() if actual_path.is_file() else None
            if actual != expected:
                current_input_changes.append({"path": relative, "expected_sha256": expected, "current_sha256": actual})
    rows = []
    for job in baseline["professions"]:
        now, before, candidates = current[job], old[job], wide[job]
        named = now["deterministic_hits"] + now["nondeterministic_hits"]
        old_named = before["deterministic_hits"] + before["nondeterministic_hits"]
        rows.append({
            "job": job, "named_true_hits": named,
            "deterministic_hits": now["deterministic_hits"],
            "remaining_named_true_hits": now["nondeterministic_hits"],
            "coverage_percent": _ratio(now["deterministic_hits"], named),
            "matched_historical_coverage_percent": _ratio(before["deterministic_hits"], old_named),
            "wide_named_candidates": candidates["named_candidate_hits"],
            "wide_joint_coverage_percent": _ratio(candidates["deterministic_hits"], candidates["named_candidate_hits"]),
        })
    final_path = run_dir / "profession_direct_damage_report.json"
    final = None
    final_incomplete = False
    if final_path.exists():
        try:
            final = json.loads(final_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            final_incomplete = True
        if final is not None:
            if signature is not None and final.get("analysis_signature") != signature:
                raise ValueError("Final report and checkpoints have different runtime signatures")
            final_manifest = {r["capture_path"]: r["capture_sha256"] for r in final["capture_manifest"]}
            if final_manifest != {r["capture"]: r["sha256"] for r in captures}:
                raise ValueError("Final report does not match completed checkpoint captures")
            if final.get("successful_captures") != len(captures):
                raise ValueError("Final report success count does not match checkpoints")
    return {
        "status": ("current_inputs_differ_not_current_acceptance" if current_input_changes else
                   "final_report_present" if final else "in_progress_not_acceptance"),
        "current_input_changes": current_input_changes,
        "usable_as_current_frozen_result": bool(final and not current_input_changes and not final["failures"]),
        "final_report_incomplete": final_incomplete,
        "completed_checkpoints": len(captures), "expected_captures": len(manifest),
        "incomplete_checkpoint_files": incomplete_files,
        "runtime_signature": signature["sha256"] if signature else None,
        "final_failures": final["failures"] if final else None,
        "comparison_warning": "Historical rows use exactly the same capture bytes, but include protocol/owner corrections; not a conservation claim or attribution-only A/B.",
        "jobs_at_95_in_completed_subset": sum(r["coverage_percent"] is not None and r["coverage_percent"] >= 95 for r in rows),
        "jobs": rows,
        "captures_with_changed_raw_invariants": sum(not c["raw_invariants_equal"] for c in captures),
        "captures_with_changed_aggregate_invariants": sum(not c["aggregate_invariants_equal"] for c in captures),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("baseline_report", type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.run_dir, args.baseline_report), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
