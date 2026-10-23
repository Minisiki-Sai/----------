from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
JOBS_DIR = ROOT / "dragon_nest_builder" / "data" / "modular" / "tables" / "skills" / "dngamer_skill_sim_reborn_split" / "jobs"
OUTPUT_DIR = ROOT / "outputs" / "burst_rules"
OUTPUT_JSON = OUTPUT_DIR / "skill_burst_rule_candidates.json"

TARGET_JOBS = {
    "月之领主",
    "剑皇",
    "时空领主",
    "黑暗女王",
    "黑暗萨满",
    "影舞者",
    "狙翎",
    "魔羽",
    "十字军",
}

KEYWORD_GROUPS: list[tuple[str, list[str]]] = [
    ("减CD/重置", ["冷却时间减少", "减少", "冷却", "缩短", "恢复冷却", "完全恢复", "重置", "充能"]),
    ("连招/快速衔接", ["快速", "衔接", "普通攻击键", "特殊攻击键", "再次输入", "追加", "额外攻击", "连按", "连续"]),
    ("状态窗口/强化", ["状态", "强化", "持续时间", "持续", "增益", "增幅"]),
    ("减抗/增伤/易伤", ["减", "抗", "受到的伤害", "伤害增加", "最终伤害", "增伤", "防御力减少"]),
    ("动作速度", ["动作速度", "攻击速度", "施法速度", "释放速度"]),
]

IMPORTANT_PATTERNS = [
    r"冷却时间[^。；\n]*?(?:减少|缩短|恢复|重置|完全恢复)[^。；\n]*",
    r"(?:减少|缩短|恢复|重置|完全恢复)[^。；\n]*?冷却[^。；\n]*",
    r"(?:快速|衔接|追加|再次输入|普通攻击键|特殊攻击键|额外攻击|连续|连按)[^。；\n]*",
    r"(?:减[^。；\n]*抗|抗[^。；\n]*减少|受到的伤害[^。；\n]*增加|伤害增加[^。；\n]*|最终伤害[^。；\n]*增加)[^。；\n]*",
    r"(?:动作速度|攻击速度|施法速度|释放速度)[^。；\n]*",
    r"(?:状态|强化)[^。；\n]*(?:持续时间|持续|伤害|冷却|增加)[^。；\n]*",
]


def norm(value: Any) -> str:
    return str(value or "").strip()


def clean_desc(raw: Any) -> str:
    text = norm(raw)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(?:div|span|p)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n+", "\n", text)
    return text.strip()


def split_lines(text: str) -> list[str]:
    parts: list[str] = []
    for line in text.splitlines():
        for piece in re.split(r"[。；;]", line):
            piece = piece.strip()
            if piece:
                parts.append(piece)
    return parts


def classify(text: str) -> list[str]:
    hits: list[str] = []
    compact = text.replace("\n", " ")
    for label, keywords in KEYWORD_GROUPS:
        if all(keyword in compact for keyword in keywords[:2]) if label == "减CD/重置" else any(keyword in compact for keyword in keywords):
            hits.append(label)
    if "冷却" in compact and any(word in compact for word in ["减少", "缩短", "恢复", "重置", "完全恢复", "充能"]):
        if "减CD/重置" not in hits:
            hits.append("减CD/重置")
    return hits


def extract_numbers(text: str) -> str:
    snippets = []
    for match in re.finditer(r"(?:减少|缩短|恢复|降低|增加|提升|附加|额外)?\s*\d+(?:\.\d+)?\s*(?:秒|s|S|%|％|次|层)", text):
        snippets.append(match.group(0).strip())
    return "；".join(dict.fromkeys(snippets))


def extract_related_skills(text: str, skill_names: set[str], current: str) -> str:
    found = [name for name in skill_names if name and name != current and name in text]
    return "；".join(sorted(set(found), key=lambda item: (len(item), item)))


def best_level_row(levels: list[dict[str, Any]]) -> dict[str, Any]:
    best: dict[str, Any] = {}
    best_key = (-1, -1)
    for row in levels:
        required = int(float(row.get("_LevelLimit") or row.get("required_level") or 0))
        level = int(float(row.get("_SkillLevel") or row.get("level") or 0))
        if required <= 70 and (required, level) > best_key:
            best = row
            best_key = (required, level)
    return best


def page_name_for_skill(data: dict[str, Any], skill_id: str) -> str:
    for group in data.get("tree", {}).get("tree", []):
        job = group.get("job", {}) if isinstance(group, dict) else {}
        for skill in group.get("skill", []) if isinstance(group.get("skill"), list) else []:
            if norm(skill.get("_SkillTableID")) == skill_id or norm(skill.get("RowId")) == skill_id:
                return norm(job.get("name"))
    return ""


def source_file_for_job(job: str) -> Path | None:
    for path in JOBS_DIR.glob("job_*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if norm(data.get("job", {}).get("name")) == job:
            return path
    return None


def extract_job(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    job = norm(data.get("job", {}).get("name"))
    skills = data.get("skills", {}) if isinstance(data.get("skills"), dict) else {}
    levels = data.get("skill_levels", {}).get("normal", {}).get("pve", {})
    skill_names = {norm(skill.get("name")) for skill in skills.values() if isinstance(skill, dict)}
    rows: list[dict[str, Any]] = []
    summary: dict[str, Any] = {"job": job, "file": path.name, "candidate_count": 0, "notes": []}

    for skill_id, skill in skills.items():
        if not isinstance(skill, dict):
            continue
        skill_name = norm(skill.get("name"))
        row = best_level_row(levels.get(skill_id, []) if isinstance(levels.get(skill_id), list) else [])
        desc = clean_desc(row.get("desc") or skill.get("desc"))
        if not desc:
            continue
        labels = classify(desc)
        if not labels:
            continue
        important_lines: list[str] = []
        for pattern in IMPORTANT_PATTERNS:
            important_lines.extend(match.group(0).strip() for match in re.finditer(pattern, desc))
        if not important_lines:
            important_lines = [line for line in split_lines(desc) if any(keyword in line for _label, group in KEYWORD_GROUPS for keyword in group)][:4]
        important_lines = list(dict.fromkeys(important_lines))
        related = extract_related_skills(desc, skill_names, skill_name)
        numbers = extract_numbers("；".join(important_lines) or desc)
        suggestion = "待确认"
        if "减CD/重置" in labels:
            suggestion = "候选：加入冷却返还/重置规则，按目标技能总CD折算"
        elif "连招/快速衔接" in labels:
            suggestion = "候选：加入强制连招/快速施法顺序"
        elif "减抗/增伤/易伤" in labels:
            suggestion = "候选：加入爆发前置或状态窗口"
        elif "动作速度" in labels:
            suggestion = "候选：加入动作速度窗口或技能施法修正"

        rows.append({
            "职业": job,
            "页签": page_name_for_skill(data, norm(skill_id)),
            "技能ID": norm(skill_id),
            "技能名": skill_name,
            "技能等级": int(float(row.get("_SkillLevel") or 0)) if row else "",
            "需求等级": int(float(row.get("_LevelLimit") or 0)) if row else "",
            "原始CD": round(float(row.get("_DelayTime") or 0) / 1000, 3) if row else "",
            "候选类型": "；".join(labels),
            "关联技能": related,
            "提取数值": numbers,
            "描述摘要": "；".join(important_lines)[:700],
            "处理建议": suggestion,
            "确认状态": "待确认",
            "数据源": path.name,
        })

    summary["candidate_count"] = len(rows)
    if job == "黑暗萨满":
        chaos = [row for row in rows if row["技能名"] == "混沌阵"]
        summary["notes"].append("混沌阵候选已提取" if chaos else "未在候选中提取到混沌阵")
    return rows, summary


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for job in sorted(TARGET_JOBS):
        path = source_file_for_job(job)
        if not path:
            summaries.append({"job": job, "file": "", "candidate_count": 0, "notes": ["未找到职业文件"]})
            continue
        rows, summary = extract_job(path)
        all_rows.extend(rows)
        summaries.append(summary)
    payload = {
        "generated_from": str(JOBS_DIR),
        "target_jobs": sorted(TARGET_JOBS),
        "rule_note": "减少技能CD按目标技能总CD折算：实际返还 = 描述返还秒数 × 当前CD系数。",
        "rows": all_rows,
        "summaries": summaries,
    }
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(OUTPUT_JSON)
    print(f"rows={len(all_rows)}")
    for item in summaries:
        print(item["job"], item["candidate_count"], "；".join(item.get("notes", [])))


if __name__ == "__main__":
    main()
