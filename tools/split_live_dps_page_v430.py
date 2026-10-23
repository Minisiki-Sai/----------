from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "dragon_nest_builder/ui/live_dps_page.py"
MANIFEST = ROOT / "docs/V430_LIVE_PAGE_SPLIT_MANIFEST.json"


@dataclass(frozen=True)
class Operation:
    start: int
    end: int
    replacement: tuple[str, ...] = ()


def node_start(node: ast.AST) -> int:
    decorators = getattr(node, "decorator_list", ())
    return min([node.lineno, *(item.lineno for item in decorators)])


def block(lines: list[str], node: ast.AST) -> str:
    return "".join(lines[node_start(node) - 1 : node.end_lineno]).rstrip() + "\n"


def rewrite(lines: list[str], operations: list[Operation]) -> str:
    result = list(lines)
    for operation in sorted(operations, key=lambda item: item.start, reverse=True):
        result[operation.start - 1 : operation.end] = list(operation.replacement)
    return "".join(result)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def import_block(module: str, names: list[str], *, binder: bool = False) -> tuple[str, ...]:
    rows = [f"from ..live_dps.{module} import (\n"]
    rows.extend(f"    {name},\n" for name in names)
    if binder:
        rows.append("    bind_ui_application as _bind_theory_ui_application,\n")
    rows.append(")\n")
    if binder:
        rows.append("_bind_theory_ui_application(QApplication)\n")
    rows.append("\n")
    return tuple(rows)


def main() -> int:
    original = SOURCE.read_text(encoding="utf-8-sig")
    if "TheoryPlanBuilderMixin" in original:
        raise SystemExit("live DPS page split already applied")
    lines = original.splitlines(keepends=True)
    tree = ast.parse(original)
    top = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    page = top["LiveDpsPage"]
    assert isinstance(page, ast.ClassDef)
    methods = {
        node.name: node
        for node in page.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    boss_names = [
        "_BossStatisticsGroup",
        "_merge_seconds_windows",
        "_boss_statistics_groups",
        "_compress_events_to_windows",
        "_compress_boss_hp_tracks_to_windows",
        "_completed_boss_group_identities",
        "_events_in_windows",
    ]
    cast_names = [
        "_TimelineOccurrence",
        "_LiveCastTimingResolver",
        "_timeline_skill_key",
        "_timeline_skill_name",
        "_timeline_skill_display_name",
        "_timeline_skill_family",
        "_is_timeline_periodic_damage",
        "_timeline_damage_hit_clusters",
        "_timeline_missing_combo_hits",
        "_timeline_unmatched_damage_hits",
        "_timeline_inferred_casts_from_damage",
        "_timeline_events_with_damage_fallback",
        "_boss_timeline_events_for_windows",
        "_build_timeline_occurrences",
        "_timeline_score_cast_events",
    ]
    theory_helper_names = [
        "_team_score_glyph_effect_key",
        "_team_score_glyph_row_key",
        "_team_score_apply_boss_preset",
        "_team_score_support_effect_group",
        "_team_score_team_dps",
        "_team_score_joint_objective_profiles",
        "_team_score_scale_objective_profiles",
        "_team_score_standalone_gain_percent",
        "_team_score_effect_changes_rotation",
        "_team_score_effects_with_required_cdr_glyphs",
        "_team_score_merge_intervals",
        "_team_score_recorded_cdr_cast_schedule",
        "_team_score_recorded_rotation_schedule",
        "_team_score_recorded_effect_cast_schedule",
        "_team_score_active_windows_to_cast_schedule",
    ]
    theory_method_names = [
        "_merge_team_score_intervals",
        "_team_score_blocked_intervals",
        "_team_score_localized_timing",
        "_team_score_cdr_cast_schedule",
        "_team_score_real_job_name",
        "_team_score_support_replan_metadata",
        "_build_team_score_theory_plans",
    ]

    boss_blocks = [block(lines, top[name]) for name in boss_names]
    boss_header = '''from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass

from .boss_hp import normalize_boss_hp_name
from .boss_recognizer import BossRecognizer
from .models import (
    BossActionEvent,
    BossHpTrackSnapshot,
    CombatEvent,
    CombatSnapshot,
    SkillCastEvent,
)
from .scoring import canonical_skill


_TERMINAL_BOSS_COMPLETION_MOVEMENT_SKILLS = frozenset(
    canonical_skill(skill)
    for skill in ("翻滚", "冲锋", "闪现", "疾闪", "滑步", "三连斩")
)

'''
    write(
        ROOT / "dragon_nest_builder/live_dps/boss_scope.py",
        boss_header + "\n\n".join(item.rstrip() for item in boss_blocks) + "\n",
    )

    cast_blocks = [block(lines, top[name]) for name in cast_names]
    cast_header = '''from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, replace

from ..data_loader import DataStore, as_number, norm
from .boss_scope import _compress_events_to_windows, _events_in_windows, _merge_seconds_windows
from .cast_timing import LiveCastTimingResolver
from .damage_ledger import is_disallowed_damage_derived_cast, requires_action_packet_for_cast
from .models import BossActionEvent, CombatEvent, SkillCastEvent
from .scoring import canonical_skill


_TIMELINE_HIT_CLUSTER_GAP_MS = 750
_TIMELINE_HIT_TO_LATE_CAST_MS = 750
_TIMELINE_MIN_CAST_DAMAGE_WINDOW_MS = 3000
_BOSS_TIMELINE_CONTEXT_SECONDS = 30.0
_BOSS_TIMELINE_PREP_SECONDS = 3.0
_TIMELINE_NON_ACTION_DAMAGE_METHODS = {
    "cast_instance_match",
    "continuation_window",
    "healing_cast",
    "learned_cast_profile",
    "supplemental_cast_owner",
    "supplemental_same_packet",
    "summon_owner",
    "sustained_cast_window",
    "status_damage_packet",
    "appended_status_first_hit",
    "unique_active_cast",
}

'''
    write(
        ROOT / "dragon_nest_builder/live_dps/cast_timeline.py",
        cast_header + "\n\n".join(item.rstrip() for item in cast_blocks) + "\n",
    )

    theory_blocks = [block(lines, top[name]) for name in theory_helper_names]
    method_blocks = [block(lines, methods[name]) for name in theory_method_names]
    theory_header = '''from __future__ import annotations

import copy
import time
from bisect import bisect_right
from collections import defaultdict
from typing import Any

from ..calculator import BuildCalculator
from ..data_loader import as_number, norm
from ..hp_conditional import compact_boss_hp_segments
from ..shared import BOSS_PRESETS, load_skill_build_defaults
from .boss_hp import resolve_fixed_boss_hp_profile
from .boss_scope import (
    _boss_statistics_groups,
    _completed_boss_group_identities,
    _compress_boss_hp_tracks_to_windows,
)
from .models import CombatEvent, CombatSnapshot
from .scoring import (
    _coverage_in_windows,
    canonical_skill,
    yao_rotation_variant,
)


_TEAM_SCORE_AURA_SKILL_KEYS = frozenset(
    {canonical_skill("元素光环"), canonical_skill("判定光环")}
)
_TEAM_SCORE_AURA_GROUP_KEY = canonical_skill("圣骑互斥光环")
_TEAM_SCORE_DEFAULT_BOSS_PRESET = "红龙佩达"
_TEAM_SCORE_OUTPUT_TEAM_OBJECTIVE_WEIGHT = 1.00
_TEAM_SCORE_DEFAULT_EXCLUDED_GLYPHS = frozenset(
    {(canonical_skill("幽灵之魂"), "增益持续")}
)


class _NoopApplication:
    @staticmethod
    def processEvents() -> None:
        return None


QApplication = _NoopApplication


def bind_ui_application(application_type: Any) -> None:
    global QApplication
    QApplication = application_type

'''
    theory_class = (
        "\n\nclass TheoryPlanBuilderMixin:\n"
        + "\n\n".join(item.rstrip() for item in method_blocks)
        + "\n"
    )
    write(
        ROOT / "dragon_nest_builder/live_dps/theory_plan_builder.py",
        theory_header
        + "\n\n".join(item.rstrip() for item in theory_blocks)
        + theory_class,
    )

    operations: list[Operation] = []
    module_sets = [
        ("boss_scope", boss_names, False),
        ("cast_timeline", cast_names, False),
        (
            "theory_plan_builder",
            ["TheoryPlanBuilderMixin", *theory_helper_names],
            True,
        ),
    ]
    extracted_top_names = {name for name in boss_names + cast_names + theory_helper_names}
    for module, names, binder in module_sets:
        original_names = [name for name in names if name in top]
        nodes = [top[name] for name in original_names]
        if not nodes:
            continue
        first = min(node_start(node) for node in nodes)
        first_node = next(node for node in nodes if node_start(node) == first)
        for node in nodes:
            replacement = import_block(module, names, binder=binder) if node is first_node else ()
            operations.append(Operation(node_start(node), node.end_lineno, replacement))

    for name in theory_method_names:
        node = methods[name]
        operations.append(Operation(node_start(node), node.end_lineno))

    header = lines[page.lineno - 1]
    operations.append(
        Operation(
            page.lineno,
            page.lineno,
            (header.replace("class LiveDpsPage(QWidget):", "class LiveDpsPage(TheoryPlanBuilderMixin, QWidget):"),),
        )
    )
    rewritten = rewrite(lines, operations)
    write(SOURCE, rewritten)

    manifest = {
        "baseline": "v4.25",
        "target": "v4.30",
        "algorithmChanges": False,
        "sourceBeforeSha256": sha(original),
        "sourceAfterSha256": sha(rewritten),
        "sourceBeforeLines": len(lines),
        "sourceAfterLines": len(rewritten.splitlines()),
        "modules": {
            "boss_scope.py": {"symbols": boss_names, "bodySha256": sha("".join(boss_blocks))},
            "cast_timeline.py": {"symbols": cast_names, "bodySha256": sha("".join(cast_blocks))},
            "theory_plan_builder.py": {
                "helpers": theory_helper_names,
                "methods": theory_method_names,
                "bodySha256": sha("".join(theory_blocks + method_blocks)),
            },
        },
    }
    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(MANIFEST)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
