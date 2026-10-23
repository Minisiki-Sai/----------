from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path
from typing import Any


DEFAULT_SOURCE_DIR = r"dragon_nest_builder\data\modular\tables\skills\dngamer_skill_sim_reborn_split"
DEFAULT_OUTPUT = r"dragon_nest_builder\data\modular\tables\skills\dngamer_skill_damage_simple.json"
DEFAULT_OUTPUT_DIR = r"dragon_nest_builder\data\modular\tables\skills\skill_damage_tiny_jobs"
DEFAULT_MAX_REQUIRED_LEVEL = 79

BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
DAMAGE_RE = re.compile(
    r"(?P<prefix>[^。；;\n]{0,36}?(?:物理攻击力|魔法攻击力|物理/魔法攻击力|攻击力))"
    r"\s*(?:为|相当于|总计|的|：|:)?\s*"
    r"(?P<percent>-?\d+(?:\.\d+)?)\s*%"
    r"\s*(?:\+\s*(?P<fixed>-?\d+(?:\.\d+)?))?"
)
REJECT_WORDS = (
    "提升", "增加", "减少", "降低", "抵抗", "概率", "移动速度", "恢复", "耐久", "防御",
    # Damage caps are not executable damage components.  In particular, the
    # base Holy Punishment says reflected damage must not exceed 100% attack;
    # only its EX skill 3306 owns the periodic 60% hit.
    "不超过", "上限",
)


def clean_desc(desc: str) -> str:
    text = BR_RE.sub("\n", desc or "")
    text = TAG_RE.sub("", text)
    text = html.unescape(text)
    text = text.replace("\u3000", " ").replace("＋", "+")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n+", "\n", text)
    return text.strip()


def damage_type(prefix: str) -> str:
    if "物理/魔法" in prefix:
        return "物理/魔法"
    if "物理" in prefix:
        return "物理"
    if "魔法" in prefix:
        return "魔法"
    return "未指定"


def damage_label(prefix: str) -> str:
    label = prefix.strip(" ，,。；;:")
    label = re.sub(r"\s+", "", label)
    return label[-24:]


def parse_damages(desc: str) -> list[dict[str, Any]]:
    text = clean_desc(desc)
    damages: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()
    for match in DAMAGE_RE.finditer(text):
        prefix = match.group("prefix")
        between = prefix[prefix.rfind("攻击力") + len("攻击力") :]
        if any(word in prefix or word in between for word in REJECT_WORDS):
            continue
        percent = int(float(match.group("percent")))
        fixed = int(float(match.group("fixed") or 0))
        if percent == 0 and fixed == 0:
            continue
        item = {
            "type": damage_type(prefix),
            "percent": percent,
            "fixed": fixed,
        }
        label = damage_label(prefix)
        if label and label not in ("物理攻击力", "魔法攻击力", "物理/魔法攻击力", "攻击力"):
            item["label"] = label
        key = (item["type"], percent, fixed)
        if key not in seen:
            seen.add(key)
            damages.append(item)
    return damages


def simplify_skill(
    skill_id: str,
    skill_name: str,
    levels: list[dict[str, Any]],
    *,
    max_required_level: int | None,
) -> dict[str, Any] | None:
    simple_levels: list[dict[str, Any]] = []
    for level in levels:
        required_level = int(level.get("_LevelLimit") or level.get("requiredLevel") or 0)
        if max_required_level is not None and required_level > max_required_level:
            continue
        damages = parse_damages(level.get("desc") or "")
        if not damages:
            continue
        simple_levels.append(
            {
                "level": int(level.get("_SkillLevel") or level.get("level") or 0),
                "required_level": required_level,
                "cooldown": round(float(level.get("_DelayTime") or level.get("cooldown") or 0) / 1000, 3),
                "damages": damages,
            }
        )
    if not simple_levels:
        return None
    return {
        "id": int(skill_id),
        "name": skill_name,
        "levels": simple_levels,
    }


def simplify_job(job_file: Path, *, max_required_level: int | None) -> dict[str, Any]:
    data = json.loads(job_file.read_text(encoding="utf-8"))
    skills = data.get("skills", {})
    normal_levels = data.get("skill_levels", {}).get("normal", {}).get("pve", {})
    master_levels = data.get("skill_levels", {}).get("master", {}).get("pve", {})

    # The compact reborn split (format 2) stores the selected job in the last
    # tree section and omits the older job/normal_skill_ids/master_skill_ids
    # convenience fields.  Derive them from the actual level tables so a
    # rebuild cannot keep a stale or hand-misaligned skill ID.
    tree_sections = data.get("tree", {}).get("tree", [])
    derived_job = (
        tree_sections[-1].get("job", {})
        if tree_sections and isinstance(tree_sections[-1], dict)
        else {}
    )
    normal_skill_ids = data.get("normal_skill_ids") or sorted(
        normal_levels, key=lambda value: int(value)
    )
    master_skill_ids = data.get("master_skill_ids") or sorted(
        master_levels, key=lambda value: int(value)
    )

    normal_skills = []
    for skill_id in normal_skill_ids:
        skill = simplify_skill(
            str(skill_id),
            skills.get(str(skill_id), {}).get("name", ""),
            normal_levels.get(str(skill_id), []),
            max_required_level=max_required_level,
        )
        if skill:
            normal_skills.append(skill)

    master_skills = []
    for skill_id in master_skill_ids:
        skill = simplify_skill(
            str(skill_id),
            skills.get(str(skill_id), {}).get("name", ""),
            master_levels.get(str(skill_id), []),
            max_required_level=max_required_level,
        )
        if skill:
            master_skills.append(skill)

    job = data.get("job") or derived_job
    return {
        "job_id": int(job.get("RowId") or job.get("id") or 0),
        "job_name": job.get("name", ""),
        "normal_skills": normal_skills,
        "master_skills": master_skills,
    }


def to_tiny_damage(damage: dict[str, Any]) -> list[Any]:
    type_map = {"物理": "P", "魔法": "M", "物理/魔法": "B", "未指定": "U"}
    return [type_map.get(damage.get("type"), "U"), damage.get("percent", 0), damage.get("fixed", 0)]


def to_tiny_skill(skill: dict[str, Any]) -> list[Any]:
    levels = []
    for level in skill.get("levels", []):
        damages = [to_tiny_damage(damage) for damage in level.get("damages", [])]
        cooldown = level.get("cooldown", 0)
        if isinstance(cooldown, float) and cooldown.is_integer():
            cooldown = int(cooldown)
        if len(damages) == 1:
            damage = damages[0]
            levels.append([level["level"], level["required_level"], cooldown, damage[0], damage[1], damage[2]])
        else:
            levels.append([level["level"], level["required_level"], cooldown, damages])
    return [skill["id"], skill["name"], levels]


def to_tiny_job(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "j": [job["job_id"], job["job_name"]],
        "n": [to_tiny_skill(skill) for skill in job.get("normal_skills", [])],
        "m": [to_tiny_skill(skill) for skill in job.get("master_skills", [])],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a compact skill damage JSON from DN Gamer skill data.")
    parser.add_argument("--source-dir", default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--job-id", type=int, help="Only simplify one second-job file, such as 23 for 剑皇.")
    parser.add_argument("--max-required-level", type=int, default=DEFAULT_MAX_REQUIRED_LEVEL)
    parser.add_argument("--tiny", action="store_true", help="Use compact array format.")
    parser.add_argument("--split-jobs", action="store_true", help="Write one output JSON per job.")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    source_dir = Path(args.source_dir)
    jobs_dir = source_dir / "jobs"
    if args.job_id:
        job_paths = [jobs_dir / f"job_{args.job_id}.json"]
    else:
        job_paths = sorted(jobs_dir.glob("job_*.json"), key=lambda p: int(p.stem.split("_")[1]))
    jobs = [simplify_job(path, max_required_level=args.max_required_level) for path in job_paths]
    if args.split_jobs:
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        index_path = output_dir / "index.json"
        index_by_job: dict[int, dict[str, Any]] = {}
        # A one-job refresh must not truncate the shared 30-job index.
        if args.job_id and index_path.exists():
            try:
                existing_index = json.loads(index_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                existing_index = {}
            index_by_job = {
                int(row.get("job_id", 0)): dict(row)
                for row in existing_index.get("jobs", [])
                if int(row.get("job_id", 0) or 0) > 0
            }
        for job in jobs:
            payload = to_tiny_job(job) if args.tiny else job
            output = output_dir / f"job_{job['job_id']}.json"
            if args.tiny:
                text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            else:
                text = json.dumps(payload, ensure_ascii=False, indent=2)
            output.write_text(text, encoding="utf-8")
            index_by_job[job["job_id"]] = {
                "job_id": job["job_id"],
                "job_name": job["job_name"],
                "file": output.name,
                "size": output.stat().st_size,
                "normal_damage_skills": len(job["normal_skills"]),
                "master_damage_skills": len(job["master_skills"]),
            }
        index = [index_by_job[key] for key in sorted(index_by_job)]
        index_path.write_text(
            json.dumps(
                {
                    "fmt": {
                        "j": "[职业ID, 职业名]",
                        "n": "普通技能",
                        "m": "专精技能",
                        "skill": "[技能ID, 技能名, 等级列表]",
                        "level": "[技能等级, 需求等级, 伤害类型, 百分比, 固定值]",
                        "type": {"P": "物理", "M": "魔法", "B": "物理/魔法", "U": "未指定"},
                        "max_req": args.max_required_level,
                    },
                    "jobs": index,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        print(f"saved split jobs: {output_dir}")
        print("jobs:", len(jobs))
        print("normal damage skills:", sum(len(job["normal_skills"]) for job in jobs))
        print("master damage skills:", sum(len(job["master_skills"]) for job in jobs))
        return
    if args.tiny:
        output_data = {
            "src": "reference.dngamer.site",
            "mode": "pve",
            "fmt": {
                "j": "[职业ID, 职业名]",
                "n": "普通技能",
                "m": "专精技能",
                "skill": "[技能ID, 技能名, 等级列表]",
                "level": "[技能等级, 需求等级, 伤害类型, 百分比, 固定值]",
                "type": {"P": "物理", "M": "魔法", "B": "物理/魔法", "U": "未指定"},
                "max_req": args.max_required_level,
            },
            "jobs": [to_tiny_job(job) for job in jobs],
        }
    else:
        output_data = {
            "source": "reference.dngamer.site",
            "mode": "pve",
            "fields": {
                "level": "技能等级",
                "required_level": "需求角色等级",
                "percent": "技能百分比伤害，已取整数",
                "fixed": "技能固定值伤害，已取整数",
                "max_required_level": f"已剔除需求等级大于 {args.max_required_level} 的等级段",
            },
            "jobs": jobs,
        }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if args.tiny:
        text = json.dumps(output_data, ensure_ascii=False, separators=(",", ":"))
    else:
        text = json.dumps(output_data, ensure_ascii=False, indent=2)
    output.write_text(text, encoding="utf-8")
    print(f"saved: {output}")
    print("jobs:", len(jobs))
    print("normal damage skills:", sum(len(job["normal_skills"]) for job in jobs))
    print("master damage skills:", sum(len(job["master_skills"]) for job in jobs))


if __name__ == "__main__":
    main()
