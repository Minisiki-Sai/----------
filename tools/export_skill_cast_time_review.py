from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.cooldown import SkillCooldownEngine
from dragon_nest_builder.data_loader import DataStore, as_number, clean_html_text, norm


OUTPUT_DIR = ROOT / "outputs" / "skill_cast_time_review"
OUTPUT_JSON = OUTPUT_DIR / "skill_cast_time_review_rows.json"

SKILL_FIXED_COOLDOWNS = {"雷电之手": 5.4, "诅咒状态": 2, "重磅鸭": 8}
SKILL_SIM_SPECIAL_COOLDOWN_SOURCES_BY_JOB = {
    "影舞者": {
        "双飞腿": (("捆绑射击", 11), ("飓风舞", 30)),
    },
    "风行者": {
        "双飞腿": (("捆绑射击", 11), ("鹰击长空", 11)),
    },
}

DAMAGE_LABELS = {"P": "物理", "M": "魔法", "B": "物理/魔法", "U": "攻击力"}


def level_cooldown(level_data: list[Any] | None) -> float:
    if not level_data:
        return 0.0
    if len(level_data) >= 6 or (len(level_data) >= 4 and isinstance(level_data[3], list)):
        return as_number(level_data[2])
    return 0.0


def level_damages(level_data: list[Any] | None) -> list[tuple[str, float, float]]:
    if not level_data:
        return []
    if len(level_data) >= 6:
        return [(str(level_data[3]), as_number(level_data[4]), as_number(level_data[5]))]
    if len(level_data) >= 4 and isinstance(level_data[3], list):
        return [(str(item[0]), as_number(item[1]), as_number(item[2])) for item in level_data[3] if len(item) >= 3]
    if len(level_data) >= 5:
        return [(str(level_data[2]), as_number(level_data[3]), as_number(level_data[4]))]
    if len(level_data) >= 3 and isinstance(level_data[2], list):
        return [(str(item[0]), as_number(item[1]), as_number(item[2])) for item in level_data[2] if len(item) >= 3]
    return []


def damage_text(level_data: list[Any] | None, multiplier: float = 1.0) -> str:
    parts: list[str] = []
    for damage_type, percent, fixed in level_damages(level_data):
        label = DAMAGE_LABELS.get(damage_type, damage_type)
        parts.append(f"{label} {percent * multiplier:.0f}%+{int(fixed * multiplier)}")
    return "；".join(parts)


def base_damage_text(level_data: list[Any] | None) -> str:
    parts: list[str] = []
    for damage_type, percent, fixed in level_damages(level_data):
        label = DAMAGE_LABELS.get(damage_type, damage_type)
        parts.append(f"{label} {percent:.0f}%+{int(fixed)}")
    return "；".join(parts)


def selected_level(levels: list[list[Any]]) -> list[Any] | None:
    candidates = [row for row in levels if isinstance(row, list) and len(row) >= 3]
    valid = [row for row in candidates if as_number(row[1], 9999) <= 70]
    return max(valid or candidates, key=lambda row: (as_number(row[1]), as_number(row[0])), default=None)


def special_effective_cooldown(store: DataStore, job: str, skill: str, external_cdr_percent: float = 0.0) -> float | None:
    sources = SKILL_SIM_SPECIAL_COOLDOWN_SOURCES_BY_JOB.get(job, {}).get(skill)
    if not sources:
        return None
    cdr = max(0.0, min(70.0, external_cdr_percent)) / 100
    rates = 0.0
    for source_skill, fallback in sources:
        base = store.skill_reference_cooldown(job, source_skill, fallback)
        cooldown = max(0.0, as_number(base) * (1 - cdr))
        if cooldown <= 0:
            return None
        rates += 1 / cooldown
    return 1 / rates if rates > 0 else None


def adjusted_base_cooldown(store: DataStore, job: str, skill: str, level_data: list[Any] | None) -> tuple[float, str]:
    adjustment = store.skill_damage_adjustment(job, skill)
    if "cooldown" in adjustment:
        return max(0.0, as_number(adjustment.get("cooldown"))), "skill_sim_adjustments.cooldown"
    return level_cooldown(level_data), "tiny技能等级CD"


def effective_cooldown(
    store: DataStore,
    engine: SkillCooldownEngine,
    job: str,
    skill: str,
    base_cd: float,
) -> tuple[float, str]:
    fixed = SKILL_FIXED_COOLDOWNS.get(skill)
    if fixed is not None:
        return max(0.0, as_number(fixed)), "固定CD，不吃-cd"
    special = special_effective_cooldown(store, job, skill, 0.0)
    if special is not None:
        return special, "特殊来源等效CD"
    rule = engine.rule(job, skill)
    if rule:
        return engine.calculate(job, skill, base_cd, 0.0), "内部等效CD规则"
    return base_cd, "原始/修正CD"


def first_job_map(store: DataStore) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in store.tables.get("character", []):
        second = norm(row.get("二转职业"))
        first = norm(row.get("一转职业"))
        if second and first:
            out[second] = first
    return out


def job_rows(store: DataStore) -> list[dict[str, Any]]:
    index = store._load_skill_damage_index()
    rows = index.get("jobs", []) if isinstance(index, dict) else []
    return [row for row in rows if isinstance(row, dict)]


def main() -> None:
    store = DataStore.load()
    engine = SkillCooldownEngine(store.skill_cooldown_rules(), store.skill_reference_cooldown)
    first_jobs = first_job_map(store)
    output_rows: list[dict[str, Any]] = []

    for job_meta in job_rows(store):
        job = norm(job_meta.get("job_name"))
        if not job:
            continue
        for entry in store._skill_damage_entries(job):
            skill = norm(entry.get("name"))
            label = norm(entry.get("label")) or skill
            levels = entry.get("levels", [])
            level_data = selected_level(levels if isinstance(levels, list) else [])
            if not level_data:
                continue
            level = int(as_number(level_data[0]))
            required_level = int(as_number(level_data[1]))
            base_cd, base_cd_source = adjusted_base_cooldown(store, job, label, level_data)
            effective_cd, effective_cd_source = effective_cooldown(store, engine, job, skill, base_cd)
            multiplier = store.skill_damage_multiplier(job, label, level)
            burning_bonus = store.skill_burning_hand_bonus(job, label, "base")
            element = store.skill_damage_element(job, label)
            desc = store.skill_damage_description(job, label, str(level))
            timing = store.skill_effect_timing(job, skill, level, max_required_level=70)
            output_rows.append(
                {
                    "一转职业": first_jobs.get(job, ""),
                    "二转职业": job,
                    "技能ID": int(as_number(entry.get("id"))),
                    "技能名称": skill,
                    "显示名称": label,
                    "等级": level,
                    "需求等级": required_level,
                    "原始/修正CD(s)": round(base_cd, 3),
                    "当前等效CD(s)": round(effective_cd, 3),
                    "CD来源": base_cd_source,
                    "等效CD来源": effective_cd_source,
                    "攻击类型": "；".join(DAMAGE_LABELS.get(d[0], d[0]) for d in level_damages(level_data)),
                    "属性": element,
                    "原始技能面板": base_damage_text(level_data),
                    "当前倍率": round(multiplier, 6),
                    "灼烧手基础加成": round(burning_bonus, 6),
                    "倍率后技能面板": damage_text(level_data, multiplier + burning_bonus),
                    "施放时间(s)-待确认": "",
                    "前摇/后摇/完整动作备注": "",
                    "是否计入施放时间修正": "是",
                    "网页DelayTime(s)": round(as_number(timing.get("cooldown")), 3),
                    "描述持续时间(s)": round(as_number(timing.get("duration")), 3),
                    "描述摘要": clean_html_text(desc).replace("\n", "；")[:700],
                }
            )

    payload = {
        "meta": {
            "purpose": "技能模拟施放时间确认表",
            "scope": "当前模拟器技能模拟下拉框内的非EX伤害技能",
            "notes": [
                "原始数据没有可靠的逐技能施放/动作耗时字段，所以施放时间留空待人工确认。",
                "DelayTime是技能冷却时间，不是施放时间。",
                "确认后可新增cast_time_seconds字段，并在技能DPS计算中引入施放时间约束。",
            ],
            "row_count": len(output_rows),
        },
        "rows": output_rows,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"WROTE {OUTPUT_JSON}")
    print(f"ROWS {len(output_rows)}")


if __name__ == "__main__":
    main()
