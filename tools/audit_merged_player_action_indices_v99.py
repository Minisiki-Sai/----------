from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.probe_merged_player_act_indices_v99 import load_merged_actions
from tools.build_dungeon_map_catalog import parse_dnt


DEFAULT_REPORT = (
    ROOT
    / "outputs/player_object_creation_v53_20260909/player_object_creation_v53.json"
)
DEFAULT_PROFILES = ROOT / "outputs/client_skill_hits/all_second_jobs_v2"
DEFAULT_OUTPUT = ROOT / "outputs/merged_player_action_indices_v99_20260910"


def _profile_catalog(profile_dir: Path, resource_root: Path):
    skill_rows = {
        int(row["RowId"]): row
        for row in parse_dnt(resource_root / "ext" / "skilltable_character.dnt")
    }

    def canonical(skill_id: int) -> int:
        visited = set()
        while skill_id in skill_rows:
            if skill_id in visited:
                raise ValueError(f"cyclic base skill: {skill_id}")
            visited.add(skill_id)
            parent = int(skill_rows[skill_id].get("_BaseSkillID", 0) or 0)
            if parent <= 0:
                break
            skill_id = parent
        return skill_id

    result = {}
    for path in sorted(profile_dir.glob("[0-9][0-9]_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        profile = payload["job_profile"]
        action_skills: dict[str, set[int]] = defaultdict(set)
        action_rows: dict[str, list[dict]] = defaultdict(list)
        classes = Counter()
        for skill in profile.get("skills", []):
            skill_id = canonical(int(skill["skill_id"]))
            for action in skill.get("player_actions", []):
                action_name = str(action.get("action_name") or "").strip()
                if not action_name:
                    continue
                action_skills[action_name.casefold()].add(skill_id)
                action_rows[action_name.casefold()].append(
                    {
                        "skill_id": skill_id,
                        "skill_name": str(skill.get("name") or skill_id),
                        "entry_action": str(action.get("entry_action") or ""),
                        "action_file": str(action.get("file") or ""),
                    }
                )
                parts = Path(str(action.get("file") or "").replace("\\", "/")).parts
                if len(parts) >= 4 and tuple(part.casefold() for part in parts[:3]) == (
                    "char", "player", parts[2].casefold()
                ):
                    classes[parts[2].casefold()] += 1
        if not classes:
            raise ValueError(f"profile has no player ACT class: {path}")
        result[str(profile["job"])] = {
            "path": path,
            "player_class": classes.most_common(1)[0][0],
            "action_skills": action_skills,
            "action_rows": action_rows,
        }
    return result


def build(report_path: Path, profile_dir: Path, resource_root: Path) -> dict:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    groups = report["aggregate"]["action_signal_resolution"]["groups"]
    profiles = _profile_catalog(profile_dir, resource_root)
    merged_by_class = {
        player_class: load_merged_actions(resource_root, player_class)
        for player_class in sorted(
            {profile["player_class"] for profile in profiles.values()}
        )
    }

    rows = []
    for group in groups:
        job = str(group["job"])
        profile = profiles.get(job)
        if profile is None:
            rows.append({**group, "status": "missing_job_profile"})
            continue
        player_class = profile["player_class"]
        merged = merged_by_class[player_class]
        action_index = int(group["action_index_i16"])
        signal_key = int(group["signal_key_i8"])
        if not 0 <= action_index < len(merged):
            rows.append(
                {
                    **group,
                    "player_class": player_class,
                    "status": "action_index_out_of_range",
                }
            )
            continue
        action = merged[action_index]
        signal_type = (
            action.signal_types[signal_key]
            if 0 <= signal_key < len(action.signal_types)
            else None
        )
        signal_payload0 = (
            action.signal_payload0[signal_key]
            if 0 <= signal_key < len(action.signal_payload0)
            else None
        )
        config_row = group.get("config_row")
        payload_matches_config = (
            config_row is not None
            and signal_payload0 == int(config_row)
        )
        skill_ids = sorted(profile["action_skills"].get(action.name.casefold(), ()))
        skill_rows = profile["action_rows"].get(action.name.casefold(), ())
        truth_ids = {
            int(skill_id)
            for skill_id, count in group.get("truth_skill_ids", {}).items()
            if int(count) > 0
        }
        latest_ids = {
            int(skill_id): int(count)
            for skill_id, count in group.get("latest_skill_ids", {}).items()
        }
        candidate = skill_ids[0] if len(skill_ids) == 1 else 0
        truth_conflicts = sorted(truth_ids - ({candidate} if candidate else set()))
        # The wire record repeats both the merged ACT signal location and the
        # concrete config row.  Requiring the type-15 payload to reproduce the
        # same config is the fail-closed proof that the indices were decoded
        # correctly.  Existing labels are intentionally not an admission
        # condition: this stronger object chain is also meant to identify and
        # audit stale/wrong historical labels.
        exact_spawn = (
            signal_type == 15
            and payload_matches_config
            and candidate > 0
        )
        current_same = int(latest_ids.get(candidate, 0)) if candidate else 0
        current_other = sum(latest_ids.values()) - current_same
        rows.append(
            {
                **group,
                "player_class": player_class,
                "merged_action": action.name,
                "merged_action_file": action.source_file,
                "merged_local_index": action.local_index,
                "signal_type": signal_type,
                "signal_payload0": signal_payload0,
                "payload_matches_config": payload_matches_config,
                "candidate_skill_ids": skill_ids,
                "candidate_skill_names": sorted(
                    {
                        str(row["skill_name"])
                        for row in skill_rows
                        if int(row["skill_id"]) in skill_ids
                    }
                ),
                "candidate_skill_id": candidate,
                "truth_conflicts": truth_conflicts,
                "exact_spawn": exact_spawn,
                "current_same_hits": current_same,
                "current_other_named_hits": current_other,
                "status": (
                    "exact_spawn"
                    if exact_spawn
                    else "not_spawn_signal"
                    if signal_type != 15
                    else "payload_config_mismatch"
                    if signal_type == 15 and not payload_matches_config
                    else "ambiguous_action"
                    if len(skill_ids) != 1
                    else "exact_spawn_truth_conflict"
                    if truth_conflicts
                    else "unresolved"
                ),
            }
        )

    status = Counter(row["status"] for row in rows)
    exact_rows = [row for row in rows if row.get("exact_spawn")]
    summary = {
        "groups": len(rows),
        "status": dict(status),
        "exact_spawn_groups": len(exact_rows),
        "exact_spawn_generations": sum(int(row["generations"]) for row in exact_rows),
        "exact_spawn_hits": sum(int(row["hits"]) for row in exact_rows),
        "exact_spawn_potential_new_hits": sum(
            int(row.get("potential_new_hits", 0)) for row in exact_rows
        ),
        "exact_spawn_current_other_named_hits": sum(
            int(row["current_other_named_hits"]) for row in exact_rows
        ),
    }
    by_job = []
    for job in sorted(profiles):
        job_rows = [row for row in exact_rows if row["job"] == job]
        if not job_rows:
            continue
        by_job.append(
            {
                "job": job,
                "groups": len(job_rows),
                "hits": sum(int(row["hits"]) for row in job_rows),
                "potential_new_hits": sum(
                    int(row.get("potential_new_hits", 0)) for row in job_rows
                ),
                "current_other_named_hits": sum(
                    int(row["current_other_named_hits"]) for row in job_rows
                ),
            }
        )
    return {
        "schema": "merged-player-action-indices-v99",
        "method": {
            "action_index": (
                "zero-based index into the client player-class ACT manifest, "
                "concatenating each listed child ACT in manifest order"
            ),
            "signal_key": "zero-based signal index in the resolved action",
            "admission": (
                "typed signal 15, payload[0:4] exactly equal to the A90370 "
                "config row, and one job-specific canonical skill action"
            ),
        },
        "summary": summary,
        "by_job": by_job,
        "rows": rows,
    }


def write_outputs(result: dict, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "merged_player_action_indices_v99.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    columns = [
        "job", "config_row", "action_index_i16", "signal_key_i8",
        "merged_action", "merged_action_file", "merged_local_index",
        "signal_type", "signal_payload0", "payload_matches_config",
        "candidate_skill_id", "candidate_skill_names",
        "generations", "hits", "potential_new_hits", "current_same_hits",
        "current_other_named_hits", "truth_skill_ids", "latest_skill_ids",
        "status",
    ]
    with (output_dir / "merged_player_action_indices_v99.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in result["rows"]:
            serial = dict(row)
            for key in ("candidate_skill_names", "truth_skill_ids", "latest_skill_ids"):
                serial[key] = json.dumps(serial.get(key), ensure_ascii=False)
            writer.writerow(serial)
    lines = [
        "# 合并玩家 ACT 动作索引审计 v99",
        "",
        "|状态|数量|",
        "|---|---:|",
        *[
            f"|{key}|{value}|"
            for key, value in sorted(result["summary"]["status"].items())
        ],
        "",
        "|职业|确定动作组|Hit|潜在新增确定 Hit|现有异名 Hit|",
        "|---|---:|---:|---:|---:|",
        *[
            f"|{row['job']}|{row['groups']}|{row['hits']}|"
            f"{row['potential_new_hits']}|{row['current_other_named_hits']}|"
            for row in result["by_job"]
        ],
        "",
    ]
    (output_dir / "merged_player_action_indices_v99.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILES)
    parser.add_argument("--resource-root", type=Path, default=Path(
        r"E:\ext0811.rar\Resource00\Resource00\resource"
    ))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build(args.report, args.profiles, args.resource_root)
    write_outputs(result, args.output_dir)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print(json.dumps(result["by_job"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
