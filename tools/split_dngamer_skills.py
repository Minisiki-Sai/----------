from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any


DEFAULT_SOURCE = r"dragon_nest_builder\data\modular\tables\skills\dngamer_skill_sim_reborn.json"
DEFAULT_OUTPUT_DIR = r"dragon_nest_builder\data\modular\tables\skills\dngamer_skill_sim_reborn_split"


def subset_levels(levels: dict[str, Any], skill_ids: set[str]) -> dict[str, dict[str, Any]]:
    return {
        "pve": {skill_id: levels.get("pve", {}).get(skill_id, []) for skill_id in sorted(skill_ids, key=int)},
        "pvp": {skill_id: levels.get("pvp", {}).get(skill_id, []) for skill_id in sorted(skill_ids, key=int)},
    }


def split_dataset(source: Path, output_dir: Path) -> None:
    data = json.loads(source.read_text(encoding="utf-8"))
    if output_dir.exists():
        shutil.rmtree(output_dir)
    jobs_dir = output_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)

    metadata = data.get("metadata", {})
    global_skills = data.get("skills", {})
    normal_levels = data.get("skill_levels", {}).get("normal", {})
    master_levels = data.get("skill_levels", {}).get("master", {})

    index_jobs: list[dict[str, Any]] = []
    for job_id, job_entry in sorted(data.get("simulator_jobs", {}).items(), key=lambda row: int(row[0])):
        normal_ids = {str(skill_id) for skill_id in job_entry.get("normal_skill_ids", [])}
        master_ids = {str(skill_id) for skill_id in job_entry.get("master_skill_ids", [])}
        all_skill_ids = normal_ids | master_ids

        job_file = f"job_{job_id}.json"
        job_data = {
            "metadata": {
                **metadata,
                "split_from": source.name,
                "split_kind": "second_job",
            },
            "job": job_entry.get("job", {}),
            "job_detail": job_entry.get("job_detail", {}),
            "tree": job_entry.get("tree", {}),
            "master_tree": job_entry.get("master_tree", {}),
            "extra": job_entry.get("extra", {}),
            "normal_skill_ids": sorted(normal_ids, key=int),
            "master_skill_ids": sorted(master_ids, key=int),
            "skills": {skill_id: global_skills.get(skill_id, {}) for skill_id in sorted(all_skill_ids, key=int)},
            "skill_levels": {
                "normal": subset_levels(normal_levels, normal_ids),
                "master": subset_levels(master_levels, master_ids),
            },
        }
        (jobs_dir / job_file).write_text(json.dumps(job_data, ensure_ascii=False, indent=2), encoding="utf-8")

        job = job_entry.get("job", {})
        index_jobs.append(
            {
                "job_id": int(job_id),
                "name": job.get("name", ""),
                "parent_job": job.get("parentJob"),
                "base_job": job.get("baseJob"),
                "base_class": job.get("baseClass"),
                "file": f"jobs/{job_file}",
                "normal_skill_count": len(normal_ids),
                "master_skill_count": len(master_ids),
            }
        )

    index = {
        "metadata": {
            **metadata,
            "split_from": source.name,
            "layout": {
                "index": "index.json",
                "jobs": "jobs/job_<二转职业ID>.json",
            },
        },
        "jobs": {
            "all": data.get("jobs", {}).get("all", []),
            "second_job_ids": data.get("jobs", {}).get("second_job_ids", []),
            "files": index_jobs,
        },
        "summary": {
            "job_file_count": len(index_jobs),
            "global_skill_count": len(global_skills),
            "normal_level_count": len(normal_levels.get("pve", {})),
            "master_level_count": len(master_levels.get("pve", {})),
            "source_errors": data.get("errors", []),
        },
    }
    (output_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Split DN Gamer skill JSON into per-second-job files.")
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    source = Path(args.source)
    output_dir = Path(args.output_dir)
    split_dataset(source, output_dir)
    print(f"saved: {output_dir}")


if __name__ == "__main__":
    main()
