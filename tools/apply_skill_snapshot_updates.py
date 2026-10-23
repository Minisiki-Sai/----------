from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge selected skills from a full simulator snapshot into split job data."
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target-dir", type=Path, required=True)
    parser.add_argument("--skill-ids", nargs="+", required=True)
    args = parser.parse_args()

    source = load_json(args.source)
    source_skills = source.get("skills", {})
    source_levels = source.get("skill_levels", {})
    requested = {str(skill_id) for skill_id in args.skill_ids}
    missing = sorted(requested - set(source_skills), key=int)
    if missing:
        raise AssertionError(f"source is missing requested skills: {missing}")

    jobs_dir = args.target_dir / "jobs"
    if not jobs_dir.is_dir():
        raise FileNotFoundError(jobs_dir)

    changed_files: list[str] = []
    changed_sections = 0
    touched_ids: set[str] = set()
    for job_path in sorted(jobs_dir.glob("job_*.json")):
        payload = load_json(job_path)
        local_ids = {str(skill_id) for skill_id in payload.get("skills", {})}
        selected = requested & local_ids
        if not selected:
            continue

        changed = False
        for skill_id in sorted(selected, key=int):
            source_skill = source_skills[skill_id]
            if payload["skills"].get(skill_id) != source_skill:
                payload["skills"][skill_id] = source_skill
                changed = True
                changed_sections += 1

            for mode in ("normal", "master"):
                for combat_type in ("pve", "pvp"):
                    source_map = (
                        source_levels.get(mode, {}).get(combat_type, {})
                    )
                    target_map = (
                        payload.setdefault("skill_levels", {})
                        .setdefault(mode, {})
                        .setdefault(combat_type, {})
                    )
                    if skill_id not in target_map or skill_id not in source_map:
                        continue
                    if target_map[skill_id] != source_map[skill_id]:
                        target_map[skill_id] = source_map[skill_id]
                        changed = True
                        changed_sections += 1

            touched_ids.add(skill_id)

        if changed:
            job_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            changed_files.append(job_path.name)

    untouched = sorted(requested - touched_ids, key=int)
    if untouched:
        raise AssertionError(f"requested skills are absent from split jobs: {untouched}")

    print(f"changed_job_files={len(changed_files)}")
    print(f"changed_sections={changed_sections}")
    print(f"skill_ids={','.join(sorted(touched_ids, key=int))}")
    print(f"job_files={','.join(changed_files)}")


if __name__ == "__main__":
    main()
