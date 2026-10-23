from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(r"E:\新建文件夹\龙之谷模拟器版本\龙之谷模拟器4.20")
OUTPUT_DIR = ROOT / "outputs" / "v420_score_batch_20260819"
RESULT_DIR = OUTPUT_DIR / "capture_results"
OUTPUT_PATH = OUTPUT_DIR / "workbook_data.json"


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def rounded(value: Any, digits: int = 4) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return round(value, digits)
    return value


def compact_json(value: Any, limit: int = 1000) -> str:
    if value in (None, "", [], {}):
        return ""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return text if len(text) <= limit else text[: limit - 1] + "…"


def first_present(source: dict[str, Any], *names: str, default: Any = "") -> Any:
    for name in names:
        value = source.get(name)
        if value is not None:
            return value
    return default


def main() -> None:
    results = [load_json(path, {}) for path in sorted(RESULT_DIR.glob("*.json"))]
    results = [item for item in results if isinstance(item, dict) and item]

    score_rows: list[list[Any]] = []
    skill_rows: list[list[Any]] = []
    support_rows: list[list[Any]] = []
    survival_rows: list[list[Any]] = []
    boss_rows: list[list[Any]] = []
    unscored_rows: list[list[Any]] = []
    duplicate_rows: list[list[Any]] = []
    capture_rows: list[list[Any]] = []
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)

    scored_capture_hashes: set[str] = set()
    score_record_count = 0
    skill_record_count = 0
    support_record_count = 0

    for item in results:
        capture_hash = str(item.get("capture_hash") or "")
        canonical = str(item.get("canonical_file") or "")
        capture_name = Path(canonical).name if canonical else ""
        source_files = [str(value) for value in item.get("source_files") or []]
        nest = str(item.get("nest") or "未识别副本")
        status = str(item.get("status") or "unknown")
        replay = item.get("replay") if isinstance(item.get("replay"), dict) else {}
        elapsed = rounded(replay.get("elapsed_seconds"), 3)
        effective = rounded(replay.get("dps_elapsed_seconds"), 3)
        total_damage = replay.get("total_damage") or 0
        total_healing = replay.get("total_healing") or 0
        players = replay.get("players") if isinstance(replay.get("players"), list) else []
        player_map = {str(player.get("source_id") or ""): player for player in players if isinstance(player, dict)}

        capture_rows.append([
            capture_name,
            capture_hash,
            status,
            nest,
            len(players),
            len(replay.get("boss_encounters") or []),
            elapsed,
            effective,
            total_damage,
            total_healing,
            item.get("file_size") or 0,
            item.get("processed_seconds") or 0,
            item.get("score_schema_version") or 0,
            len(source_files),
            str(item.get("error") or ""),
        ])

        if len(source_files) > 1:
            canonical_source = Path(source_files[0]).name
            for duplicate in source_files[1:]:
                duplicate_rows.append([Path(duplicate).name, canonical_source, capture_hash, len(source_files)])

        for boss in replay.get("boss_encounters") or []:
            if not isinstance(boss, dict):
                continue
            stats = boss.get("base_stats") if isinstance(boss.get("base_stats"), dict) else {}
            boss_rows.append([
                capture_name,
                capture_hash,
                nest,
                boss.get("name") or "未知Boss",
                boss.get("max_hp") or 0,
                boss.get("damage") or 0,
                boss.get("hits") or 0,
                boss.get("critical_hits") or 0,
                rounded((boss.get("first_ms") or 0) / 1000, 3),
                rounded((boss.get("last_ms") or 0) / 1000, 3),
                compact_json(boss.get("phase_windows"), 600),
                ", ".join(str(value) for value in boss.get("target_ids") or []),
                boss.get("confidence") or "",
                stats.get("template_id") or "",
                stats.get("level") or "",
                stats.get("physical_defense") or 0,
                stats.get("magic_defense") or 0,
                stats.get("critical_resistance") or 0,
                stats.get("fire_resistance") or 0,
                stats.get("ice_resistance") or 0,
                stats.get("light_resistance") or 0,
                stats.get("dark_resistance") or 0,
            ])

        if status != "scored":
            unscored_rows.append([
                capture_name,
                capture_hash,
                status,
                nest,
                item.get("file_size") or 0,
                elapsed,
                total_damage,
                len(players),
                str(item.get("error") or "未形成可评分战斗记录"),
            ])
            continue

        scored_capture_hashes.add(capture_hash)
        details = item.get("details") if isinstance(item.get("details"), list) else []
        detail_map = {str(detail.get("source_id") or ""): detail for detail in details if isinstance(detail, dict)}
        for summary in item.get("score_rows") or []:
            if not isinstance(summary, dict):
                continue
            source_id = str(summary.get("sourceId") or "")
            detail = detail_map.get(source_id, {})
            player = player_map.get(source_id, {})
            job = str(summary.get("job") or detail.get("job") or player.get("job") or "未知职业")
            score_record_count += 1
            grouped[(nest, job)].append(summary)
            score_rows.append([
                capture_name,
                capture_hash,
                item.get("modified_at") or "",
                nest,
                source_id,
                summary.get("name") or detail.get("name") or player.get("name") or "",
                job,
                summary.get("role") or detail.get("role") or "",
                summary.get("roleReason") or detail.get("role_reason") or "",
                summary.get("grade") or detail.get("grade") or "",
                rounded(summary.get("totalScore"), 3),
                rounded(summary.get("outputScore"), 3),
                rounded(summary.get("supportScore"), 3),
                rounded(summary.get("specialtyScore"), 3),
                rounded(summary.get("specialtyContribution"), 3),
                rounded(summary.get("survivalPenalty"), 3),
                rounded(summary.get("outputWeight"), 4),
                rounded(summary.get("supportWeight"), 4),
                rounded(summary.get("specialtyWeight"), 4),
                summary.get("specialtyLabel") or "",
                rounded(summary.get("theoryDamageCompletion"), 4),
                rounded(summary.get("burstUsage"), 4),
                rounded(summary.get("coverageCompletion"), 4),
                rounded(summary.get("contributionCompletion"), 4),
                rounded(summary.get("actualStandardPower"), 3),
                rounded(summary.get("theoryStandardPower"), 3),
                player.get("damage") or 0,
                player.get("healing") or 0,
                rounded(player.get("dps"), 3),
                player.get("hits") or 0,
                player.get("critical_hits") or 0,
                rounded(player.get("critical_rate"), 5),
                rounded(player.get("active_seconds"), 3),
                player.get("incoming_damage") or 0,
                "；".join(str(value) for value in summary.get("notes") or []),
            ])

            for skill in detail.get("skill_details") or []:
                if not isinstance(skill, dict):
                    continue
                skill_record_count += 1
                skill_rows.append([
                    capture_name,
                    capture_hash,
                    nest,
                    source_id,
                    job,
                    skill.get("skill_name") or "",
                    skill.get("actual_count") or 0,
                    skill.get("theory_count") or 0,
                    rounded(skill.get("actual_power"), 3),
                    rounded(skill.get("theory_power"), 3),
                    rounded(skill.get("completion"), 5),
                    bool(skill.get("high_value")),
                    rounded(skill.get("actual_duration"), 3),
                    rounded(skill.get("expected_duration"), 3),
                    rounded(skill.get("duration_utilization"), 5),
                    rounded(skill.get("proportional_output"), 5),
                    rounded(skill.get("usage_rate"), 5),
                    bool(skill.get("excluded_from_score")),
                ])

            for support in detail.get("support_details") or []:
                if not isinstance(support, dict):
                    continue
                support_record_count += 1
                support_rows.append([
                    capture_name,
                    capture_hash,
                    nest,
                    source_id,
                    job,
                    support.get("skill_name") or "",
                    support.get("effect_key") or "",
                    support.get("kind") or "",
                    rounded(support.get("magnitude"), 4),
                    rounded(support.get("actual_coverage"), 5),
                    rounded(support.get("theory_coverage"), 5),
                    rounded(support.get("completion"), 5),
                    rounded(support.get("score_weight"), 5),
                    rounded(support.get("actual_contribution"), 5),
                    rounded(support.get("theory_contribution"), 5),
                    rounded(support.get("expected_duration"), 3),
                    bool(support.get("included_in_score")),
                    rounded(support.get("actual_covered_damage_share"), 5),
                    rounded(support.get("theory_covered_damage_share"), 5),
                    compact_json(support.get("actual_intervals"), 800),
                    compact_json(support.get("theory_intervals"), 800),
                ])

            survival = detail.get("survival_detail") if isinstance(detail.get("survival_detail"), dict) else {}
            survival_rows.append([
                capture_name,
                capture_hash,
                nest,
                source_id,
                job,
                rounded(detail.get("survival_penalty"), 3),
                survival.get("incoming_damage") or 0,
                survival.get("hit_count") or 0,
                survival.get("incident_count") or 0,
                survival.get("shared_incident_count") or 0,
                survival.get("team_mechanic_incident_count") or 0,
                survival.get("targeted_mechanic_incident_count") or 0,
                survival.get("avoidable_incident_count") or 0,
                survival.get("uncertain_incident_count") or 0,
                rounded(survival.get("weighted_incidents"), 4),
                rounded(survival.get("free_incidents"), 4),
                rounded(survival.get("evaluated_seconds"), 3),
                rounded(survival.get("role_factor"), 4),
                survival.get("death_count") or 0,
                survival.get("reference_hp") or 0,
                survival.get("avoidable_damage") or 0,
                rounded(survival.get("avoidable_hp_loss_per_minute"), 5),
                rounded(survival.get("damage_penalty"), 4),
                survival.get("danger_incident_count") or 0,
                rounded(survival.get("danger_penalty"), 4),
                rounded(survival.get("interruption_loss"), 4),
                rounded(survival.get("interruption_loss_ratio"), 5),
                rounded(survival.get("interruption_penalty"), 4),
                survival.get("verified_interruption_count") or 0,
                rounded(survival.get("death_penalty"), 4),
                rounded(survival.get("non_death_penalty_cap"), 4),
                rounded(survival.get("uncapped_non_death_penalty"), 4),
                compact_json(survival.get("hp_shadow"), 1200),
            ])

    summary_rows: list[list[Any]] = []
    for (nest, job), rows in sorted(grouped.items()):
        def avg(field: str) -> float:
            values = [float(row[field]) for row in rows if isinstance(row.get(field), (int, float))]
            return round(sum(values) / len(values), 3) if values else 0.0

        summary_rows.append([
            nest,
            job,
            len(rows),
            avg("totalScore"),
            avg("outputScore"),
            avg("supportScore"),
            avg("specialtyScore"),
            avg("survivalPenalty"),
            avg("theoryDamageCompletion"),
            avg("burstUsage"),
            avg("coverageCompletion"),
            avg("contributionCompletion"),
        ])

    upload = load_json(OUTPUT_DIR / "upload_result.json", {}) or {}
    index = load_json(OUTPUT_DIR / "batch_index.json", {}) or {}
    payload = {
        "meta": {
            "simulator_version": "4.20",
            "score_schema_version": 20260819,
            "source_directory": r"C:\Users\gcg59\Documents\DragonNestDpsCapture\live_dps_diagnostics",
            "file_count": int(index.get("file_count") or sum(len(item.get("source_files") or []) for item in results)),
            "unique_capture_count": int(index.get("unique_capture_count") or len(results)),
            "scored_capture_count": len(scored_capture_hashes),
            "unscored_capture_count": len(unscored_rows),
            "failed_capture_count": sum(1 for item in results if item.get("status") == "failed"),
            "score_record_count": score_record_count,
            "skill_record_count": skill_record_count,
            "support_record_count": support_record_count,
            "duplicate_file_count": len(duplicate_rows),
            "upload": upload,
        },
        "headers": {
            "captures": ["记录文件", "抓包哈希", "状态", "副本", "角色数", "Boss数", "记录时长(s)", "有效战斗(s)", "总伤害", "总治疗", "文件大小(B)", "处理耗时(s)", "评分版本", "同哈希文件数", "错误/说明"],
            "scores": ["记录文件", "抓包哈希", "文件时间", "副本", "实时ID", "记录角色名", "职业", "定位", "定位依据", "等级", "总分", "输出分", "辅助分", "特色分", "特色贡献", "生存扣分", "输出权重", "辅助权重", "特色权重", "特色项目", "标准化输出完成率", "爆发/加速兑现率", "覆盖完成率", "贡献完成率", "实战标准伤害", "理论标准伤害", "原始伤害", "治疗", "DPS", "命中", "暴击", "暴击率", "活跃时间(s)", "承伤", "备注"],
            "skills": ["记录文件", "抓包哈希", "副本", "实时ID", "职业", "技能", "实战次数", "理论次数", "实战标准伤害", "理论标准伤害", "完成率", "高价值", "实战持续(s)", "理论持续(s)", "持续利用率", "比例输出", "使用率", "评分豁免"],
            "supports": ["记录文件", "抓包哈希", "副本", "实时ID", "职业", "来源技能", "效果键", "类型", "数值", "实战覆盖", "理论覆盖", "完成率", "评分权重", "实战贡献", "理论贡献", "预期持续(s)", "参与评分", "实战覆盖伤害占比", "理论覆盖伤害占比", "实战区间", "理论区间"],
            "survival": ["记录文件", "抓包哈希", "副本", "实时ID", "职业", "生存扣分", "承伤", "受击数", "机制事件", "多人事件", "6-8人事件", "3-5人事件", "1-2人事件", "不确定事件", "加权事件", "免费事件", "评估时长(s)", "职业系数", "死亡", "参考HP", "可规避伤害", "每分钟可规避HP损失", "伤害扣分", "危险血量事件", "危险扣分", "循环损失", "循环损失率", "循环扣分", "已验证打断", "死亡扣分", "非死亡扣分上限", "未封顶非死亡扣分", "HP影子明细"],
            "bosses": ["记录文件", "抓包哈希", "副本", "Boss", "血量库HP", "承受伤害", "命中", "暴击", "首次(s)", "末次(s)", "阶段区间", "实时目标ID", "可信度", "模板ID", "等级", "物防", "魔防", "致命抵抗", "火抗", "水抗", "光抗", "暗抗"],
            "unscored": ["记录文件", "抓包哈希", "状态", "副本", "文件大小(B)", "记录时长(s)", "总伤害", "角色数", "原因"],
            "duplicates": ["重复文件", "保留文件", "抓包哈希", "同哈希文件数"],
            "summary": ["副本", "职业", "样本角色数", "平均总分", "平均输出分", "平均辅助分", "平均特色分", "平均生存扣分", "平均输出完成率", "平均爆发兑现率", "平均覆盖完成率", "平均贡献完成率"],
        },
        "rows": {
            "captures": capture_rows,
            "scores": score_rows,
            "skills": skill_rows,
            "supports": support_rows,
            "survival": survival_rows,
            "bosses": boss_rows,
            "unscored": unscored_rows,
            "duplicates": duplicate_rows,
            "summary": summary_rows,
        },
    }
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(payload["meta"], ensure_ascii=False, indent=2))
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()
