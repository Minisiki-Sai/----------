from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
INPUT_JSON = ROOT / "outputs" / "burst_rules" / "skill_burst_rule_candidates.json"
OUTPUT_JSON = ROOT / "outputs" / "burst_rules" / "skill_burst_rule_candidates_curated.json"


def norm(value: Any) -> str:
    return str(value or "").strip()


def has_any(text: str, words: list[str]) -> bool:
    return any(word in text for word in words)


def reason_for_row(row: dict[str, Any]) -> tuple[bool, str, str]:
    skill = norm(row.get("技能名"))
    desc = norm(row.get("描述摘要"))
    related = norm(row.get("关联技能"))
    text = desc

    if not desc:
        return False, "删除", "无有效描述"

    # These are generic movement/defensive/common descriptions, not burst-rotation mechanisms.
    generic_skill_names = {
        "翻滚", "闪现", "回避", "浮空调整", "起身斩", "起身踢", "冲锋", "疾闪",
        "滑步挥破", "凌空踢", "踢飞", "升龙斩", "高地人",
    }
    if skill in generic_skill_names:
        return False, "删除", "通用位移/起身/生存类技能，非爆发机制"

    generic_bad = [
        "冷却时间",
        "连续按下两次方向键",
        "Shift键",
        "快速奔跑",
        "生命不会下降",
        "在眩晕状态",
        "姿势",
        "翻滚",
        "回避",
        "起身",
    ]
    if not related and has_any(text, generic_bad) and not has_any(text, ["减少", "恢复", "重置", "完全恢复", "追加", "普通攻击键", "特殊攻击键"]):
        return False, "删除", "通用位移/生存/状态说明，非技能机制"

    if re.fullmatch(r"冷却时间\s*\d+(?:\.\d+)?秒?", text):
        return False, "删除", "只描述自身冷却时间，不是冷却机制"

    cd_words = ["冷却时间减少", "冷却时间缩短", "减少", "缩短", "恢复冷却", "完全恢复", "重置", "充能"]
    has_cd = "冷却" in text and has_any(text, cd_words)
    has_reset = has_any(text, ["完全恢复", "重置", "充能"])
    has_specific_relation = bool(related)
    has_self_target = has_any(text, ["本技能", "该技能"])

    if has_cd and (has_specific_relation or has_self_target or has_reset):
        status = "保留"
        note = "明确冷却减少/恢复/重置，后续按目标技能总CD折算"
        if not has_specific_relation and has_self_target:
            note += "；目标为本技能"
        return True, status, note

    if (
        has_any(text, ["减抗", "抗减少", "抗性减少", "受到的伤害增加", "伤害增加", "最终伤害增加"])
        or re.search(r"减[^；。\n]*抗", text)
    ):
        return True, "保留", "明确减抗/增伤前置，爆发模拟应优先覆盖"

    combo_words = ["快速释放", "快速使用", "衔接", "追加", "额外攻击", "再次输入", "普通攻击键", "特殊攻击键", "连续释放"]
    if has_specific_relation and has_any(text, combo_words):
        return True, "保留", "技能间连招/追加/快速衔接候选"

    # Some EX rows mention only self extra attacks; those can affect burst timing even without another named skill.
    if skill.upper().endswith("EX") and has_any(text, ["追加", "额外攻击", "再次输入", "普通攻击键", "特殊攻击键"]):
        return True, "复核", "EX追加/特殊输入候选，但缺少明确关联技能"

    # Keep major named relation windows that do not always include cooldown language.
    special_relation_words = ["使用", "后", "期间", "状态下"]
    if has_specific_relation and has_any(text, special_relation_words) and has_any(text, ["伤害", "最终伤害", "增伤", "强化", "减抗", "抗"]):
        return True, "复核", "技能间状态窗口候选，需确认是否影响爆发排序"

    return False, "删除", "未体现技能间CD/重置/连招关系"


def main() -> None:
    data = json.loads(INPUT_JSON.read_text(encoding="utf-8"))
    rows = data.get("rows", [])
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for row in rows:
        keep, status, note = reason_for_row(row)
        new_row = dict(row)
        new_row["确认状态"] = status
        new_row["筛选说明"] = note
        if keep:
            kept.append(new_row)
        else:
            removed.append(new_row)

    if not any(norm(row.get("职业")) == "黑暗萨满" and norm(row.get("技能名")) == "混沌阵" for row in kept):
        kept.append({
            "职业": "黑暗萨满",
            "页签": "黑暗萨满",
            "技能ID": "",
            "技能名": "混沌阵",
            "技能等级": "",
            "需求等级": "",
            "原始CD": 30,
            "候选类型": "减抗/增伤/易伤",
            "关联技能": "",
            "提取数值": "减暗抗30%",
            "描述摘要": "混沌阵造成减暗抗30%的爆发前置效果",
            "处理建议": "保留：加入爆发前置，优先覆盖减暗抗",
            "确认状态": "保留",
            "数据源": "TEAM_MANUAL_DEBUFFS",
            "筛选说明": "手动补入：当前模拟器团队减抗表已有此效果",
        })

    by_job: dict[str, dict[str, int]] = {}
    for row in kept:
        job = norm(row.get("职业"))
        bucket = by_job.setdefault(job, {"保留": 0, "复核": 0})
        bucket[norm(row.get("确认状态"))] = bucket.get(norm(row.get("确认状态")), 0) + 1

    payload = {
        **data,
        "curation_note": (
            "自动精简：删除明显不是技能间机制的条目；保留明确减少/重置/恢复特定技能CD、"
            "技能间快速衔接/追加、以及少量需要人工复核的状态窗口。"
        ),
        "rows": kept,
        "removed_rows": removed,
        "curated_summary": {
            "source_rows": len(rows),
            "kept_rows": len(kept),
            "removed_rows": len(removed),
            "by_job": by_job,
        },
    }
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(OUTPUT_JSON)
    print(json.dumps(payload["curated_summary"], ensure_ascii=False, indent=2))
    for job in sorted(by_job):
        examples = [r for r in kept if norm(r.get("职业")) == job][:5]
        print(job, "；".join(f"{r['技能名']}({r['确认状态']})" for r in examples))


if __name__ == "__main__":
    main()
