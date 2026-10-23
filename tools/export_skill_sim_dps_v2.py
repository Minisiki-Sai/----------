from __future__ import annotations

import json
import os
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QComboBox, QDoubleSpinBox

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.app import (  # noqa: E402
    BuildEditor,
    FREE_STAT_PERCENT_ATTRS,
    SKILL_SIM_ROWS,
    as_number,
)
from dragon_nest_builder.calculator import BuildCalculator  # noqa: E402
from dragon_nest_builder.data_loader import DataStore, norm  # noqa: E402


OUTPUT_JSON = ROOT / "exports" / "skill_sim_dps_60L_all_jobs_v3_23.json"
ELEMENT_ATTRS = ["火攻%", "水攻%", "光攻%", "暗攻%"]


def item_text(table, row: int, col: int) -> str:
    item = table.item(row, col)
    return item.text() if item else ""


def set_double_spin_value(widget: Any, value: float) -> None:
    if isinstance(widget, QDoubleSpinBox):
        widget.blockSignals(True)
        widget.setValue(float(value))
        widget.blockSignals(False)


def clear_skill_buffs(editor: BuildEditor) -> None:
    for row in range(editor.buff_table.rowCount()):
        skill_widget = editor.buff_table.cellWidget(row, 0)
        if isinstance(skill_widget, QComboBox):
            editor._set_combo_current(skill_widget, "无")
        level_widget = editor.buff_table.cellWidget(row, 1)
        if isinstance(level_widget, QComboBox):
            editor._replace_combo_values(level_widget, ["无"], "无")


def set_all_cards_l(editor: BuildEditor) -> None:
    for row in range(editor.card_table.rowCount()):
        widget = editor.card_table.cellWidget(row, 1)
        if isinstance(widget, QComboBox):
            editor._set_combo_current(widget, "L")


def set_glyph_a_attack_extra(editor: BuildEditor) -> None:
    profile = editor._job_profile()
    attack = profile.get("attack_attr") or "物攻"
    if isinstance(editor.auto_glyph_a_extra, QComboBox):
        editor._set_combo_current(editor.auto_glyph_a_extra, attack)
    editor.apply_auto_glyph_a_extra()


def equalize_element_attack_to_max(editor: BuildEditor, calculator: BuildCalculator) -> dict[str, float]:
    build = editor.collect_build()
    _rows, summary = calculator.calculate(build)
    current = {attr: as_number(summary.get(attr)) for attr in ELEMENT_ATTRS}
    target = max(current.values()) if current else 0.0
    for row, attr in enumerate(FREE_STAT_PERCENT_ATTRS):
        if attr in ELEMENT_ATTRS:
            set_double_spin_value(editor.free_stat_percent_table.cellWidget(row, 1), max(0.0, target - current.get(attr, 0.0)))
    editor.refresh()
    build = editor.collect_build()
    _rows, summary = calculator.calculate(build)
    return {attr: as_number(summary.get(attr)) for attr in ELEMENT_ATTRS}


def calculate_skill_dps(
    editor: BuildEditor,
    store: DataStore,
    summary: dict[str, float],
) -> tuple[float, float, list[dict[str, object]]]:
    weighted_panel = editor._skill_weighted_panel_profile()
    profile = editor._job_profile()
    preferred_type = None if weighted_panel else ("M" if profile.get("attack_attr") == "魔攻" else "P")
    total = 0.0
    rows: list[dict[str, object]] = []
    for row in range(SKILL_SIM_ROWS):
        skill = editor.combo_text(editor.skill_sim_table, row, 0)
        level = editor.combo_text(editor.skill_sim_table, row, 1)
        level_data = store.skill_damage_level_data(editor.second_job.currentText(), skill, level)
        cooldown_widget = editor.skill_sim_table.cellWidget(row, 3)
        cooldown = float(cooldown_widget.value()) if isinstance(cooldown_widget, QDoubleSpinBox) else 0.0
        multiplier_widget = editor.skill_sim_table.cellWidget(row, 4)
        base_multiplier = float(multiplier_widget.value()) if isinstance(multiplier_widget, QDoubleSpinBox) else 0.0
        burning_bonus = editor._skill_burning_hand_bonus(skill)
        display_multiplier = base_multiplier + burning_bonus
        element = store.skill_damage_element(editor.second_job.currentText(), skill)
        element_multiplier = editor._skill_element_display_multiplier(element, summary)
        dps_multiplier = editor._skill_sim_dps_multiplier(element, profile)
        effective = 0.0
        dps = 0.0
        dps_after = 0.0
        if level_data and cooldown > 0:
            effective = editor._skill_level_effective(
                level_data,
                summary,
                multiplier=display_multiplier,
                preferred_type=preferred_type,
                weighted_panel=weighted_panel,
            )
            dps = effective / cooldown * dps_multiplier
            dps_after = dps * element_multiplier
            total += dps
        rows.append(
            {
                "skill": skill,
                "level": level,
                "required_level": int(as_number(level_data[1])) if level_data else "",
                "cooldown": cooldown,
                "base_multiplier": base_multiplier,
                "burning_hand_bonus": burning_bonus,
                "display_multiplier": display_multiplier,
                "element": element or "无",
                "effective_percent": effective,
                "element_multiplier": element_multiplier,
                "dps_percent": dps,
                "dps_after_element": dps_after,
                "burning_hand_text": item_text(editor.skill_sim_table, row, 5),
                "skill_value_text": item_text(editor.skill_sim_table, row, 6),
                "effective_text": item_text(editor.skill_sim_table, row, 7),
                "dps_text": item_text(editor.skill_sim_table, row, 8),
            }
        )
    expected_panel = editor._skill_weighted_panel_value(summary, weighted_panel) if weighted_panel else as_number(summary.get("effective_panel"))
    return total, expected_panel * total / 100, rows


def main() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    store = DataStore.load()
    calculator = BuildCalculator(store)

    jobs: list[tuple[str, str]] = []
    for first in store.first_jobs():
        for second in store.second_jobs(first):
            if second and second != "无":
                jobs.append((first, second))

    summary_rows: list[dict[str, object]] = []
    detail_rows: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []

    for index, (first, second) in enumerate(jobs, 1):
        try:
            build = deepcopy(store.defaults["build1"])
            build["first_job"] = first
            build["second_job"] = second
            build["buffs"] = []
            editor = BuildEditor(second, store, calculator, build)
            editor._export_skill_sim_page = editor._skill_sim_page()
            app.processEvents()

            editor.auto_equip("60L", "all")
            set_all_cards_l(editor)
            editor.auto_glyphs()
            set_glyph_a_attack_extra(editor)
            editor.auto_runes()
            editor.auto_misc()
            clear_skill_buffs(editor)
            element_attacks = equalize_element_attack_to_max(editor, calculator)
            editor.auto_fill_skill_sim()
            editor.refresh()

            final_build = editor.collect_build()
            _result_rows, summary = calculator.calculate(final_build)
            editor.update_skill_sim_preview(summary)
            dps_total, expected_dps, skill_rows = calculate_skill_dps(editor, store, summary)
            profile = editor._job_profile()
            element_attr = (profile.get("element_attr") or "").replace("%", "")

            summary_rows.append(
                {
                    "first_job": first,
                    "second_job": second,
                    "attack_type": profile.get("attack_attr", ""),
                    "main_stat": profile.get("main_stat", ""),
                    "element": element_attr or "无",
                    "effective_panel": as_number(summary.get("effective_panel")),
                    "physical_average": as_number(summary.get("physical_average")),
                    "magic_average": as_number(summary.get("magic_average")),
                    "final_percent": as_number(summary.get("final_percent")),
                    "crit_percent": as_number(summary.get("crit_percent")),
                    "crit_damage_percent": as_number(summary.get("crit_damage_percent")),
                    "fire_attack": element_attacks.get("火攻%", 0.0),
                    "water_attack": element_attacks.get("水攻%", 0.0),
                    "light_attack": element_attacks.get("光攻%", 0.0),
                    "dark_attack": element_attacks.get("暗攻%", 0.0),
                    "dps_total": dps_total,
                    "expected_dps": expected_dps,
                }
            )

            for row, data in enumerate(skill_rows):
                detail_rows.append(
                    {
                        "first_job": first,
                        "second_job": second,
                        "row": row + 1,
                        **data,
                    }
                )
            editor.deleteLater()
            print(f"[{index}/{len(jobs)}] {first}-{second}: {dps_total:.3f}%/s")
        except Exception as exc:  # pragma: no cover - export diagnostics
            errors.append({"first_job": first, "second_job": second, "error": repr(exc)})
            print(f"ERROR {first}-{second}: {exc!r}")

    summary_rows.sort(key=lambda row: float(row["dps_total"]), reverse=True)
    payload = {
        "config": {
            "equipment": "60L赞龙装备 + 60L赞龙首饰",
            "cards": "全部卡片等级 L；卡片总属性加成等级保持默认",
            "glyphs": "一键纹章；A纹章额外属性尽量使用当前职业对应攻击，不能选择时保留无",
            "misc": "其他页一键全选后清空技能buff",
            "element_attack": "火/水/光/暗攻击统一补到该配装当前四属性攻击最大值",
            "skill_sim": "执行一键模拟，取10个技能的属性前DPS%/s总和；属性后DPS仅作为明细参考",
        },
        "summary": summary_rows,
        "details": detail_rows,
        "errors": errors,
    }
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"SAVED {OUTPUT_JSON}")
    print(f"jobs={len(summary_rows)} details={len(detail_rows)} errors={len(errors)}")


if __name__ == "__main__":
    main()
