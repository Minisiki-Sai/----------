from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener


API_BASE = "https://dngamer.site/dnwiki/api/"
REFERER = "https://reference.dngamer.site/"
DEFAULT_REGION = "re"
DEFAULT_LEVEL = 70
DEFAULT_WORKERS = 6


class DNGamerClient:
    def __init__(self, *, region: str, timeout: int = 40) -> None:
        self.region = region
        self.timeout = timeout
        self.opener = build_opener()
        self.token = ""

    def login(self) -> None:
        data = self.get("login.php", include_region=False, authorized=False)
        self.token = data["token"]

    def get(self, endpoint: str, *, include_region: bool = True, authorized: bool = True, **params: Any) -> Any:
        query = dict(params)
        if include_region:
            query.setdefault("lan", self.region)
        url = API_BASE + endpoint
        if query:
            url += ("&" if "?" in url else "?") + urlencode(query)
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": REFERER,
            "Accept": "application/json,text/plain,*/*",
        }
        if authorized and self.token:
            headers["Authorization"] = self.token

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                request = Request(url, headers=headers)
                with self.opener.open(request, timeout=self.timeout) as response:
                    body = response.read().decode("utf-8", errors="replace")
                return json.loads(body)
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise RuntimeError(f"Fetch failed: {url}\n{body[:500] if 'body' in locals() else ''}") from last_error


def extract_skill_ids(tree_data: dict[str, Any] | None, *, master: bool = False) -> list[str]:
    if not tree_data:
        return []
    key = "_SkillID" if master else "_SkillTableID"
    ids: list[str] = []
    for tree in tree_data.get("tree", []):
        for skill in tree.get("skill") or []:
            value = skill.get(key)
            if value is not None:
                ids.append(str(value))
    return sorted(set(ids), key=lambda x: int(x))


def collect_skill_meta(tree_data: dict[str, Any] | None, out: dict[str, dict[str, Any]], *, master: bool = False) -> None:
    if not tree_data:
        return
    key = "_SkillID" if master else "_SkillTableID"
    for tree in tree_data.get("tree", []):
        job = tree.get("job", {})
        for skill in tree.get("skill") or []:
            value = skill.get(key)
            if value is None:
                continue
            skill_id = str(value)
            existing = out.setdefault(skill_id, {})
            existing.update(skill)
            existing.setdefault("appears_in_jobs", [])
            existing.setdefault("tree_jobs", [])
            if job.get("id") is not None and job.get("id") not in existing["tree_jobs"]:
                existing["tree_jobs"].append(job.get("id"))


def merge_level_maps(target: dict[str, Any], source: dict[str, Any] | None) -> None:
    if not source:
        return
    for mode in ("pve", "pvp"):
        mode_data = source.get(mode) or {}
        target_mode = target.setdefault(mode, {})
        for skill_id, levels in mode_data.items():
            target_mode.setdefault(str(skill_id), levels)


def fetch_job_data(region: str, token: str, level: int, job: dict[str, Any], index: int, total: int) -> tuple[int, dict[str, Any], dict[str, Any] | None]:
    client = DNGamerClient(region=region)
    client.token = token
    job_id = int(job["RowId"])
    print(f"[{index:02d}/{total}] fetch {job_id} {job['name']}", flush=True)

    job_entry: dict[str, Any] = {"job": job}
    try:
        job_entry["job_detail"] = client.get("job2.php", id=job_id)
        normal_tree = client.get("skillsims.php", id=job_id, type="jobtree", level=level)
        master_tree = client.get("skillsims.php", id=job_id, type="master")
        extra = client.get("skillsims.php", id=job_id, type="extra")
        normal_levels = client.get("skillsims.php", id=job_id, type="level")
        master_levels = client.get("skillsims.php", id=job_id, type="masterlevel")

        job_entry["tree"] = normal_tree
        job_entry["master_tree"] = master_tree
        job_entry["extra"] = extra
        job_entry["normal_skill_ids"] = extract_skill_ids(normal_tree)
        job_entry["master_skill_ids"] = extract_skill_ids(master_tree, master=True)
        job_entry["_normal_levels"] = normal_levels
        job_entry["_master_levels"] = master_levels
        print(f"[{index:02d}/{total}] done  {job_id} {job['name']}", flush=True)
        return job_id, job_entry, None
    except Exception as exc:
        print(f"[{index:02d}/{total}] error {job_id} {job['name']}: {exc}", flush=True)
        return job_id, job_entry, {"job_id": job_id, "job_name": job.get("name"), "error": str(exc)}


def build_dataset(region: str, level: int, workers: int) -> dict[str, Any]:
    client = DNGamerClient(region=region)
    client.login()

    all_jobs = client.get("table.php", table="job", spec="0,1,2")
    second_jobs = [job for job in all_jobs if int(job.get("jobNumber", -1)) == 2]

    dataset: dict[str, Any] = {
        "metadata": {
            "source": REFERER,
            "api_base": API_BASE,
            "region": region,
            "actor_level": level,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "notes": [
                "Data fetched from DN聚集地/reference.dngamer.site public simulator API.",
                "Only second-job entries expose the full skill simulator in the current frontend.",
                "Skill level maps are deduplicated globally and referenced by each job.",
                "The reborn hero skill endpoint currently returns a server-side table error, so it is not fetched.",
            ],
        },
        "jobs": {
            "all": all_jobs,
            "second_job_ids": [job["RowId"] for job in second_jobs],
        },
        "simulator_jobs": {},
        "skills": {},
        "skill_levels": {"normal": {"pve": {}, "pvp": {}}, "master": {"pve": {}, "pvp": {}}},
        "errors": [],
    }

    results: list[tuple[int, dict[str, Any], dict[str, Any] | None]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(fetch_job_data, region, client.token, level, job, index, len(second_jobs)): job
            for index, job in enumerate(second_jobs, start=1)
        }
        for future in as_completed(future_map):
            results.append(future.result())

    for job_id, job_entry, error in sorted(results, key=lambda row: row[0]):
        normal_levels = job_entry.pop("_normal_levels", None)
        master_levels = job_entry.pop("_master_levels", None)
        if error:
            dataset["errors"].append(error)

        collect_skill_meta(job_entry.get("tree"), dataset["skills"], master=False)
        collect_skill_meta(job_entry.get("master_tree"), dataset["skills"], master=True)
        for skill_id in set(job_entry.get("normal_skill_ids", []) + job_entry.get("master_skill_ids", [])):
            dataset["skills"].setdefault(skill_id, {}).setdefault("appears_in_jobs", [])
            if job_id not in dataset["skills"][skill_id]["appears_in_jobs"]:
                dataset["skills"][skill_id]["appears_in_jobs"].append(job_id)

        merge_level_maps(dataset["skill_levels"]["normal"], normal_levels)
        merge_level_maps(dataset["skill_levels"]["master"], master_levels)
        dataset["simulator_jobs"][str(job_id)] = job_entry

    return dataset


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch DN Gamer skill simulator data.")
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--level", type=int, default=DEFAULT_LEVEL)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument(
        "--output",
        default=r"dragon_nest_builder\data\modular\tables\skills\dngamer_skill_sim_reborn.json",
    )
    parser.add_argument("--pretty", action="store_true", help="Write formatted JSON instead of compact JSON.")
    args = parser.parse_args()

    dataset = build_dataset(args.region, args.level, args.workers)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        if args.pretty:
            json.dump(dataset, handle, ensure_ascii=False, indent=2)
        else:
            json.dump(dataset, handle, ensure_ascii=False, separators=(",", ":"))
    print(f"saved: {output}")
    print(f"jobs: {len(dataset['simulator_jobs'])}, skills: {len(dataset['skills'])}")
    print(
        "normal levels:",
        len(dataset["skill_levels"]["normal"]["pve"]),
        "master levels:",
        len(dataset["skill_levels"]["master"]["pve"]),
    )
    if dataset["errors"]:
        print("errors:", len(dataset["errors"]))


if __name__ == "__main__":
    main()
