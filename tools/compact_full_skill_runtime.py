from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = (
    PROJECT_ROOT
    / "dragon_nest_builder"
    / "data"
    / "modular"
    / "tables"
    / "skills"
    / "dngamer_skill_sim_reborn_split"
)

SKILL_FIELDS = (
    "RowId",
    "_SkillTableID",
    "_SkillID",
    "name",
    "maxlevel",
    "_ParentSkillID1",
    "_ParentSkillID2",
    "_ParentSkillID3",
    "_NeedParentSkillLevel1",
    "_NeedParentSkillLevel2",
    "_NeedParentSkillLevel3",
    "_TreeSlotIndex",
    "_TreeSortIndex",
    "_IconImageIndex",
    "_SkillType",
    "_DurationType",
    "_TargetType",
    "_Element",
    "_DuplicatedSkillType",
    "_GlobalSkillGroup",
    "_NeedBasicSP1",
    "_NeedFirstSP1",
    "_NeedSecondSP1",
    "default",
    "weapon1",
    "weapon2",
)

LEVEL_FIELDS = (
    "_SkillLevel",
    "_LevelLimit",
    "_NeedSkillPoint",
    "_DecreaseSP",
    "_DelayTime",
    "desc",
)

JOB_FIELDS = ("id", "name", "sp", "index")


def selected(source: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: source[field] for field in fields if field in source}


def compact_tree(source: Any) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {"totalsp": 0, "tree": []}
    groups: list[dict[str, Any]] = []
    for group in source.get("tree", []):
        if not isinstance(group, dict):
            continue
        job = group.get("job", {})
        skills = group.get("skill", [])
        groups.append(
            {
                "job": selected(job, JOB_FIELDS) if isinstance(job, dict) else {},
                "skill": [selected(skill, SKILL_FIELDS) for skill in skills if isinstance(skill, dict)],
            }
        )
    return {"totalsp": source.get("totalsp", 0), "tree": groups}


def compact_levels(source: Any) -> dict[str, Any]:
    output: dict[str, Any] = {}
    if not isinstance(source, dict):
        return output
    for mode in ("normal", "master"):
        mode_data = source.get(mode, {})
        pve = mode_data.get("pve", {}) if isinstance(mode_data, dict) else {}
        if not isinstance(pve, dict):
            continue
        output[mode] = {
            "pve": {
                str(skill_id): [selected(row, LEVEL_FIELDS) for row in rows if isinstance(row, dict)]
                for skill_id, rows in pve.items()
                if isinstance(rows, list)
            }
        }
    return output


def compact_job(source: dict[str, Any]) -> dict[str, Any]:
    skills = source.get("skills", {})
    return {
        "format": 2,
        "tree": compact_tree(source.get("tree")),
        "master_tree": compact_tree(source.get("master_tree")),
        "skills": {
            str(skill_id): selected(skill, SKILL_FIELDS)
            for skill_id, skill in skills.items()
            if isinstance(skill, dict)
        }
        if isinstance(skills, dict)
        else {},
        "skill_levels": compact_levels(source.get("skill_levels")),
    }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="精简模拟器运行时所需的网页技能原始数据")
    parser.add_argument("destination", type=Path, nargs="?", help="精简数据输出目录")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="原始职业拆分数据目录")
    parser.add_argument(
        "--install",
        action="store_true",
        help="验证后原子替换项目运行数据，并删除已被模块化数据替代的重复总表",
    )
    args = parser.parse_args()

    source = args.source.resolve()
    if args.install:
        expected_source = DEFAULT_SOURCE.resolve()
        if source != expected_source:
            raise SystemExit(f"--install 只允许替换当前项目的运行目录：{expected_source}")
        destination = source.with_name(f"{source.name}_compact_tmp")
    elif args.destination is not None:
        destination = args.destination.resolve()
    else:
        raise SystemExit("请提供输出目录，或使用 --install 安装到当前项目。")
    if source == destination:
        raise SystemExit("输出目录不能与输入目录相同，请先输出到临时目录并验证后再替换。")
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)

    index_path = source / "index.json"
    if not index_path.exists():
        raise SystemExit(f"缺少索引文件：{index_path}")
    write_json(destination / "index.json", json.loads(index_path.read_text(encoding="utf-8")))

    source_bytes = index_path.stat().st_size
    output_bytes = (destination / "index.json").stat().st_size
    count = 0
    for source_path in sorted((source / "jobs").glob("job_*.json")):
        source_bytes += source_path.stat().st_size
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        output_path = destination / "jobs" / source_path.name
        write_json(output_path, compact_job(payload))
        output_bytes += output_path.stat().st_size
        count += 1

    print(f"jobs={count}")
    print(f"source={source_bytes / 1024 / 1024:.2f} MB")
    print(f"output={output_bytes / 1024 / 1024:.2f} MB")
    print(f"saved={(source_bytes - output_bytes) / 1024 / 1024:.2f} MB")

    if args.install:
        data_root = PROJECT_ROOT / "dragon_nest_builder" / "data"
        redundant_files = (
            data_root / "dragon_nest_data.json",
            source.parent / "dngamer_skill_sim_reborn.json",
        )
        redundant_dirs = (source.parent / "dngamer_skill_sim_runtime_test",)
        shutil.rmtree(source)
        destination.replace(source)
        removed = 0
        for path in redundant_files:
            resolved = path.resolve()
            if data_root.resolve() not in resolved.parents:
                raise SystemExit(f"拒绝删除项目数据目录外的文件：{resolved}")
            if resolved.exists():
                removed += resolved.stat().st_size
                resolved.unlink()
        for path in redundant_dirs:
            resolved = path.resolve()
            if data_root.resolve() not in resolved.parents:
                raise SystemExit(f"拒绝删除项目数据目录外的目录：{resolved}")
            if resolved.exists() and resolved != source:
                removed += sum(item.stat().st_size for item in resolved.rglob("*") if item.is_file())
                shutil.rmtree(resolved)
        print(f"installed={source}")
        print(f"removed_redundant={removed / 1024 / 1024:.2f} MB")


if __name__ == "__main__":
    main()
