from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _deduplicate_levels(levels: dict[str, Any] | None, skill_ids: list[str]) -> dict[str, dict[str, Any]]:
    source = levels if isinstance(levels, dict) else {}
    result: dict[str, dict[str, Any]] = {"pve": {}, "pvp": {}}
    for mode in ("pve", "pvp"):
        mode_rows = source.get(mode, {}) if isinstance(source.get(mode, {}), dict) else {}
        for skill_id in sorted({str(value) for value in skill_ids}, key=int):
            rows = mode_rows.get(skill_id, [])
            by_level: dict[int, dict[str, Any]] = {}
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                level = int(row.get("_SkillLevel") or row.get("level") or 0)
                # The API can retain the old and new row for the same skill
                # level after a balance patch. The last row is the live row.
                by_level[level] = row
            result[mode][skill_id] = [by_level[level] for level in sorted(by_level)]
    return result


def _write_json(path: Path, payload: Any, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=None if compact else 2,
            separators=(",", ":") if compact else None,
        ),
        encoding="utf-8",
    )


def update_job(root: Path, job_id: int) -> None:
    tools_dir = root / "tools"
    sys.path.insert(0, str(tools_dir))
    from fetch_dngamer_skills import (  # noqa: PLC0415
        DNGamerClient,
        collect_skill_meta,
        fetch_job_data,
    )
    from simplify_dngamer_skill_damage import simplify_job, to_tiny_job  # noqa: PLC0415

    client = DNGamerClient(region="re")
    client.login()
    all_jobs = client.get("table.php", table="job", spec="0,1,2")
    job = next((row for row in all_jobs if int(row.get("RowId", 0)) == job_id), None)
    if not isinstance(job, dict):
        raise RuntimeError(f"DN聚集地没有职业 {job_id}")

    _fetched_id, entry, error = fetch_job_data("re", client.token, 70, job, 1, 1)
    if error:
        raise RuntimeError(error["error"])
    normal_levels = entry.pop("_normal_levels", None)
    master_levels = entry.pop("_master_levels", None)
    normal_ids = [str(value) for value in entry.get("normal_skill_ids", [])]
    master_ids = [str(value) for value in entry.get("master_skill_ids", [])]

    skills: dict[str, dict[str, Any]] = {}
    collect_skill_meta(entry.get("tree"), skills, master=False)
    collect_skill_meta(entry.get("master_tree"), skills, master=True)
    for skill_id in {*normal_ids, *master_ids}:
        skill = skills.setdefault(skill_id, {})
        skill.setdefault("appears_in_jobs", [])
        if job_id not in skill["appears_in_jobs"]:
            skill["appears_in_jobs"].append(job_id)

    split_dir = (
        root
        / "dragon_nest_builder"
        / "data"
        / "modular"
        / "tables"
        / "skills"
        / "dngamer_skill_sim_reborn_split"
    )
    job_path = split_dir / "jobs" / f"job_{job_id}.json"
    old = json.loads(job_path.read_text(encoding="utf-8"))
    metadata = dict(old.get("metadata", {}))
    metadata["job_updated_at"] = datetime.now(timezone.utc).isoformat()
    metadata["job_update_source"] = "DN聚集地实时技能模拟器 API"
    payload = {
        "metadata": metadata,
        "job": entry.get("job", {}),
        "job_detail": entry.get("job_detail", {}),
        "tree": entry.get("tree", {}),
        "master_tree": entry.get("master_tree", {}),
        "extra": entry.get("extra", {}),
        "normal_skill_ids": sorted(set(normal_ids), key=int),
        "master_skill_ids": sorted(set(master_ids), key=int),
        "skills": {skill_id: skills.get(skill_id, {}) for skill_id in sorted({*normal_ids, *master_ids}, key=int)},
        "skill_levels": {
            "normal": _deduplicate_levels(normal_levels, normal_ids),
            "master": _deduplicate_levels(master_levels, master_ids),
        },
    }
    _write_json(job_path, payload)

    index_path = split_dir / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    for row in index.get("jobs", {}).get("files", []):
        if int(row.get("job_id", 0)) == job_id:
            row["normal_skill_count"] = len(normal_ids)
            row["master_skill_count"] = len(master_ids)
            break
    _write_json(index_path, index)

    tiny_dir = split_dir.parent / "skill_damage_tiny_jobs"
    tiny_path = tiny_dir / f"job_{job_id}.json"
    tiny_payload = to_tiny_job(simplify_job(job_path, max_required_level=79))
    _write_json(tiny_path, tiny_payload, compact=True)
    tiny_index_path = tiny_dir / "index.json"
    tiny_index = json.loads(tiny_index_path.read_text(encoding="utf-8"))
    tiny_job = simplify_job(job_path, max_required_level=79)
    for row in tiny_index.get("jobs", []):
        if int(row.get("job_id", 0)) == job_id:
            row.update(
                {
                    "job_name": tiny_job["job_name"],
                    "file": tiny_path.name,
                    "size": tiny_path.stat().st_size,
                    "normal_damage_skills": len(tiny_job["normal_skills"]),
                    "master_damage_skills": len(tiny_job["master_skills"]),
                }
            )
            break
    _write_json(tiny_index_path, tiny_index, compact=True)
    print(f"updated {job_id} {job.get('name')}: {job_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Update selected split DN Gamer skill jobs in-place.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--job-id", type=int, action="append", required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    for job_id in dict.fromkeys(args.job_id):
        update_job(root, job_id)


if __name__ == "__main__":
    main()
