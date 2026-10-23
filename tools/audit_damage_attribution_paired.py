"""Replay immutable captures against saved/current modules without swapping files."""
from __future__ import annotations

import argparse
import hashlib
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MODULES = {
    "dragon_nest_builder.live_dps.generated_attack_instance_specs_v576": "generated_attack_instance_specs_v576.py",
    "dragon_nest_builder.live_dps.protocol_v576": "protocol_v576.py",
    "dragon_nest_builder.live_dps.damage_object_origins": "damage_object_origins.py",
    "dragon_nest_builder.live_dps.v576.core_decoder": "core_decoder.py",
}

OPTIONAL_MODULES = {
    "dragon_nest_builder.live_dps.v576.actor_state": "actor_state.py",
    "dragon_nest_builder.live_dps.entity_owner_evidence": "entity_owner_evidence.py",
    "dragon_nest_builder.live_dps.summon_attack_runtime": "summon_attack_runtime.py",
    "dragon_nest_builder.live_dps.summon_self_attack_evidence_v26": "summon_self_attack_evidence_v26.py",
    "dragon_nest_builder.live_dps.generated_summon_self_attack_specs_v26": "generated_summon_self_attack_specs_v26.py",
    "dragon_nest_builder.live_dps.generated_mirror_self_attack_specs_v26": "generated_mirror_self_attack_specs_v26.py",
    "dragon_nest_builder.live_dps.damage_object_v25_shaman_illusion": "damage_object_v25_shaman_illusion.py",
    "dragon_nest_builder.live_dps.timeline_evidence": "timeline_evidence.py",
    "dragon_nest_builder.live_dps.cast_timeline": "cast_timeline.py",
    "dragon_nest_builder.live_dps.damage_attribution": "damage_attribution.py",
    "dragon_nest_builder.live_dps.models": "models.py",
    "dragon_nest_builder.live_dps.sources": "sources.py",
    "dragon_nest_builder.live_dps.replay_normalization": "replay_normalization.py",
    "dragon_nest_builder.live_dps.v576.status_decoder": "status_decoder.py",
    "dragon_nest_builder.live_dps.udp_direction": "udp_direction.py",
}


def snapshot_modules(directory: Path | None) -> dict[str, str]:
    return {
        **MODULES,
        **{
            name: filename for name, filename in OPTIONAL_MODULES.items()
            if directory is not None and (directory / filename).is_file()
        },
    }


class BaselineFinder(importlib.abc.MetaPathFinder):
    def __init__(self, directory: Path, modules: dict[str, str] | None = None):
        self.directory = directory
        self.modules = dict(modules if modules is not None else snapshot_modules(directory))

    def find_spec(self, fullname, path=None, target=None):
        filename = self.modules.get(fullname)
        if filename:
            return importlib.util.spec_from_file_location(fullname, self.directory / filename)
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--baseline-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--worker", choices=("before", "after"))
    parser.add_argument("--candidate-dir", type=Path)
    parser.add_argument("--reuse-before-dir", type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.worker:
        runtime_dir = args.baseline_dir if args.worker == "before" else args.candidate_dir
        if runtime_dir is not None:
            for filename in snapshot_modules(runtime_dir).values():
                if not (runtime_dir / filename).is_file():
                    raise FileNotFoundError(filename)
            sys.meta_path.insert(0, BaselineFinder(runtime_dir))
        from tools.audit_direct_damage_by_profession import _audit_capture, _second_jobs
        capture = args.captures[0].resolve()
        report = _audit_capture(str(capture), tuple(_second_jobs()))
        def module_path(name):
            module = sys.modules[name]
            # Mixins bind the host namespace, which overwrites module.__file__.
            # The function code still identifies the file actually executed.
            if name.endswith(".core_decoder"):
                return Path(module.V576DecoderCoreMixin.feed_events.__code__.co_filename).resolve()
            if name.endswith(".status_decoder"):
                return Path(module.V576StatusDecoderMixin._supplemental_embedded_direct_damage_events.__code__.co_filename).resolve()
            if name.endswith(".actor_state"):
                return Path(module.V576ActorStateMixin._reject_extreme_damage_outlier.__code__.co_filename).resolve()
            if name.endswith(".replay_normalization"):
                return Path(module._canonicalize_live_message.__code__.co_filename).resolve()
            return Path(module.__file__).resolve()
        report["runtime_modules"] = {
            name: {"path": str(module_path(name)),
                   "sha256": hashlib.sha256(module_path(name).read_bytes()).hexdigest()}
            for name in snapshot_modules(runtime_dir)
        }
        (args.output_dir / f"{args.worker}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0

    result = {"baseline_dir": str(args.baseline_dir.resolve()), "captures": []}
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    for capture in args.captures:
        key = hashlib.sha256(str(capture.resolve()).encode("utf-8")).hexdigest()[:12]
        directory = args.output_dir / key
        for mode in ("before", "after"):
            saved_before = args.reuse_before_dir / key / "before.json" if args.reuse_before_dir else None
            if mode == "before" and saved_before and saved_before.is_file():
                saved = json.loads(saved_before.read_text(encoding="utf-8"))
                core_name = "dragon_nest_builder.live_dps.v576.core_decoder"
                core_meta = saved.get("runtime_modules", {}).get(core_name, {})
                live_core = ROOT / "dragon_nest_builder/live_dps/v576/core_decoder.py"
                baseline_core = args.baseline_dir / "core_decoder.py"
                live_sources = ROOT / "dragon_nest_builder/live_dps/sources.py"
                if (
                    Path(core_meta.get("path", "")).resolve() == live_sources.resolve()
                    and core_meta.get("sha256") == hashlib.sha256(live_sources.read_bytes()).hexdigest()
                    and live_core.read_bytes() == baseline_core.read_bytes()
                ):
                    saved["legacy_core_metadata"] = dict(core_meta)
                    saved["runtime_modules"][core_name] = {
                        "path": str(baseline_core.resolve()),
                        "sha256": hashlib.sha256(baseline_core.read_bytes()).hexdigest(),
                    }
                valid = (
                    saved.get("capture_sha256") == hashlib.sha256(capture.read_bytes()).hexdigest()
                    and "hit_evidence_sha256" in saved.get("raw_source_invariants", {})
                    and all(saved.get("runtime_modules", {}).get(name, {}).get("sha256")
                            == hashlib.sha256((args.baseline_dir / filename).read_bytes()).hexdigest()
                            for name, filename in snapshot_modules(args.baseline_dir).items())
                )
                if not valid:
                    raise ValueError(f"Saved baseline evidence differs: {saved_before}")
                directory.mkdir(parents=True, exist_ok=True)
                (directory / "before.json").write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
                continue
            subprocess.run([
                sys.executable, str(Path(__file__).resolve()), str(capture),
                "--baseline-dir", str(args.baseline_dir), "--output-dir", str(directory),
                "--worker", mode,
                *(["--candidate-dir", str(args.candidate_dir)] if args.candidate_dir else []),
            ], check=True, env=env, cwd=ROOT)
        before, after = (json.loads((directory / f"{mode}.json").read_text(encoding="utf-8"))
                         for mode in ("before", "after"))
        # A reused baseline may predate the correction of mixed owner labels.
        # Reclassify both reports identically in memory; leave saved raw
        # reports, runtime hashes and physical evidence untouched.
        from tools.audit_direct_damage_by_profession import reclassify_checkpoint_report
        before, after = map(reclassify_checkpoint_report, (before, after))
        row = {"capture": str(capture), "checks": {}, "professions": [],
               "comparison_classification": {
                   "before": before["reclassification"],
                   "after": after["reclassification"],
               }}
        for field in ("capture_sha256", "raw_source_invariants", "aggregated_invariants"):
            equal = before[field] == after[field] if field == "capture_sha256" else all(
                before[field][key] == after[field][key] for key in ("rows", "damage", "sha256"))
            row["checks"][field] = {"equal": equal,
                                    "before": before[field], "after": after[field]}
        row["real_hit_evidence_changes"] = {
            field: {"before": before[field]["real_hits"], "after": after[field]["real_hits"],
                    "changed": before[field]["hit_evidence_sha256"] != after[field]["hit_evidence_sha256"]}
            for field in ("raw_source_invariants", "aggregated_invariants")
        }
        for job in sorted(set(before["profession_summary"]) | set(after["profession_summary"])):
            left = before["profession_summary"].get(job, {})
            right = after["profession_summary"].get(job, {})
            def rate(counts):
                return round(100 * counts.get("deterministic_hits", 0) / max(1, counts.get("total_direct_hits", 0)), 4)
            row["professions"].append({"job": job, "before": left, "after": right,
                                       "before_pct": rate(left), "after_pct": rate(right)})
        result["captures"].append(row)
        result["all_conserved"] = all(check["equal"] for item in result["captures"] for check in item["checks"].values())
        (args.output_dir / "paired_report.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"capture": capture.name, "conserved": all(x["equal"] for x in row["checks"].values()),
                          "professions": [{k: x[k] for k in ("job", "before_pct", "after_pct")} for x in row["professions"]]}, ensure_ascii=False), flush=True)
    return 0 if result["all_conserved"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
