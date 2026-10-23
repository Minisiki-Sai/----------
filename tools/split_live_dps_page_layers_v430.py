from __future__ import annotations

import ast
import hashlib
import json
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "dragon_nest_builder" / "ui" / "live_dps_page.py"
UI_DIR = SOURCE.parent
MANIFEST = ROOT / "docs" / "V430_LIVE_PAGE_LAYER_SPLIT_MANIFEST.json"


METHOD_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "live_dps_page_layout.py",
        "LiveDpsLayoutMixin",
        (
            "__init__",
            "_create_view_progress",
            "_view_progress_key",
            "_view_progress_label",
            "_set_view_progress",
            "_begin_view_progress",
            "_advance_view_progress",
            "_finish_view_progress",
            "_build_ui",
            "_build_party_info_page",
            "_new_party_detail_table",
            "_build_team_score_page",
            "_ensure_team_score_splitter_layout",
            "_build_boss_statistics_page",
            "showEvent",
            "resizeEvent",
            "ensure_visible_layout",
            "_warm_team_score_workers",
            "_schedule_layout_finalize",
            "_finalize_visible_layout",
            "_resize_boss_effect_overview_columns",
            "_resize_player_buff_overview_columns",
            "_live_dps_tab_changed",
            "_live_dps_curve_tab_changed",
            "_boss_statistics_tab_changed",
            "_boss_stats_effect_detail_tab_changed",
            "_schedule_ui_refresh",
            "_run_scheduled_ui_refresh",
        ),
    ),
    (
        "live_dps_page_boss_statistics.py",
        "LiveDpsBossStatisticsMixin",
        (
            "_timeline_zoom_changed",
            "_curve_pixels_changed",
            "_damage_curve_view_changed",
            "_damage_curve_scroll_changed",
            "_sync_curve_scrollbar",
            "_boss_effect_curve_view_changed",
            "_player_buff_curve_view_changed",
            "_player_buff_time_selected",
            "_player_buff_return_latest",
            "_boss_stats_damage_curve_view_changed",
            "_boss_stats_effect_curve_view_changed",
            "_boss_stats_buff_curve_view_changed",
            "_set_boss_stats_realtime_seconds",
            "_boss_stats_time_selected",
            "_boss_stats_time_hovered",
            "_apply_boss_stats_hover_seconds",
            "_boss_stats_realtime_follow_latest_clicked",
            "_fill_boss_stats_realtime_table",
            "_sync_boss_statistics_selector",
            "_update_boss_statistics_selection_label",
            "_boss_statistics_selection_changed",
            "_set_all_boss_statistics_selected",
            "_boss_stats_content_filter_changed",
            "_sync_boss_stats_player_combo",
            "_fill_boss_statistics_damage_curve",
            "_ensure_boss_hp_context",
            "_fill_boss_statistics",
            "_fill_boss_statistics_summary",
            "_fill_boss_statistics_skills",
            "_fill_boss_statistics_effects",
            "_fill_boss_statistics_buffs",
            "_timeline_events_for_snapshot",
            "_timestamp_in_windows",
            "_fill_boss_action_timeline",
            "_boss_stats_action_filter_changed",
            "_boss_stats_action_zoom_changed",
            "_boss_stats_action_view_changed",
            "_update_boss_stats_action_summary",
            "_fill_boss_stats_action_table",
            "_fill_boss_statistics_timeline",
            "_sync_boss_stats_timeline_filters",
            "_rebuild_boss_stats_timeline_skill_combo",
            "_boss_stats_timeline_filter_changed",
            "_boss_stats_timeline_zoom_changed",
            "_boss_stats_timeline_view_changed",
            "_update_boss_stats_timeline_summary",
            "_fill_boss_stats_timeline_table",
        ),
    ),
    (
        "live_dps_page_capture.py",
        "LiveDpsCaptureMixin",
        (
            "_open_history_folder",
            "_player_display_details",
            "_reset_session_display_state",
            "_boss_target_details",
            "_toggle_capture",
            "set_npcap_available",
            "_check_or_install_npcap",
            "_update_team_score_sync_status",
            "_open_team_score_sync_settings",
            "_flush_team_score_offline_queue",
            "_schedule_team_score_sync_generation",
            "_generate_team_score_for_sync",
            "_set_team_score_capture_path",
            "_team_score_sync_source_key",
            "_queue_team_score_sync",
            "_start_team_score_sync",
            "_team_score_sync_finished",
            "_team_score_sync_thread_finished",
            "_start_capture",
            "_auto_capture_enabled_changed",
            "_poll_auto_capture",
            "_game_transport_ports",
            "_stop_capture",
            "_shutdown_capture",
            "_poll_capture",
            "_finish_capture",
            "_load_replay",
            "_load_replay_path",
            "_replay_load_progressed",
            "_cancel_replay_load",
            "_replay_load_succeeded",
            "_refresh_loaded_replay_view",
            "_replay_load_failed",
            "_replay_load_cancelled",
            "_finish_replay_load_ui",
            "_replay_thread_finished",
            "_clear",
            "_poll",
        ),
    ),
    (
        "live_dps_page_party.py",
        "LiveDpsPartyMixin",
        (
            "_item",
            "_refresh",
            "_refresh_current_view",
            "_fill_party_info",
            "_party_members_for_snapshot",
            "_party_actor_key",
            "_party_profile_actor_id",
            "_party_profile_roster_id",
            "_party_member_selection_changed",
            "_party_profile_for_member",
            "_render_party_member_profile",
            "_clear_party_detail_tables",
            "_set_party_detail_rows",
            "_party_passive_buff_rows",
            "_format_party_passive_effect",
        ),
    ),
    (
        "live_dps_page_views.py",
        "LiveDpsViewsMixin",
        (
            "_fill_timeline",
            "_timeline_player_changed",
            "_rebuild_timeline_skill_combo",
            "_timeline_filter_changed",
            "_timeline_view_changed",
            "_timeline_scroll_changed",
            "_update_timeline_summary",
            "_fill_timeline_event_table",
            "_boss_effect_curve_selection_changed",
            "_set_curve_filter_panel_visible",
            "_boss_stats_effect_curve_selection_changed",
            "_boss_stats_buff_filter_changed",
            "_boss_detail_tab_changed",
            "_boss_effect_overview_target_changed",
            "_sync_boss_effect_overview_target_control",
            "_player_buff_detail_tab_changed",
            "_schedule_boss_detail_refresh",
            "_schedule_player_buff_detail_refresh",
            "_player_buff_filter_changed",
            "_player_buff_filter_matches",
            "_sync_player_buff_filter_controls",
            "_sync_boss_stats_buff_filter_controls",
            "_status_value_text",
            "_player_buff_effect_text",
            "_player_buff_value_text",
            "_boss_effect_output_windows",
            "_boss_freeze_summary_rows",
            "_fill_boss_effects",
            "_fill_boss_effect_details",
            "_fill_player_buffs",
            "_fill_player_buff_details",
            "_fill_players",
            "_player_changed",
            "_fill_damage_curve",
            "_fill_skills",
        ),
    ),
    (
        "live_dps_page_scoring.py",
        "LiveDpsScoringMixin",
        (
            "_team_score_grade_colors",
            "_team_score_default_boss_preset_name",
            "_sync_team_score_boss_preset_selector",
            "_team_score_resolved_boss_preset_name",
            "_sync_team_score_boss_selector",
            "_team_score_display_windows",
            "_team_score_specialty_windows",
            "_team_score_scope_label",
            "_team_score_boss_changed",
            "_team_score_boss_preset_changed",
            "_team_score_glyph_options",
            "_team_score_glyph_job_for_score",
            "_team_score_glyph_rows_from_controls",
            "_populate_team_score_glyph_controls",
            "_team_score_glyph_selection_changed",
            "_invalidate_team_score_glyph_results",
            "_apply_team_score_glyphs",
            "_reset_team_score_glyphs",
            "_run_pending_team_score_refresh",
            "_fill_team_scores",
            "_team_score_selection_changed",
            "_team_score_output_skill_filter_changed",
            "_show_team_score_detail",
            "_fill_team_score_specialty",
            "_team_score_shared_actual_timeline",
            "_team_score_output_effect_rows",
            "_team_score_output_metric_rows",
            "_fill_team_score_evidence",
            "_team_score_support_selection_changed",
            "_show_team_score_support_timeline",
        ),
    ),
)


def _source_segment(lines: list[str], node: ast.AST) -> str:
    start = min(
        [getattr(node, "lineno")]
        + [getattr(item, "lineno") for item in getattr(node, "decorator_list", ())]
    )
    return textwrap.dedent(
        "".join(lines[start - 1 : getattr(node, "end_lineno")])
    )


def _write(path: Path, text: str) -> None:
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")


def main() -> None:
    source_text = SOURCE.read_text(encoding="utf-8")
    lines = source_text.splitlines(keepends=True)
    tree = ast.parse(source_text)
    page = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "LiveDpsPage"
    )
    methods = {
        node.name: node
        for node in page.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    requested = [name for _, _, names in METHOD_GROUPS for name in names]
    missing = sorted(set(methods) - set(requested))
    unknown = sorted(set(requested) - set(methods))
    if missing or unknown:
        raise RuntimeError(f"method grouping mismatch: missing={missing}, unknown={unknown}")

    replay_worker = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "_ReplayLoadWorker"
    )
    player_buff_curve = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "PlayerBuffCurve"
    )
    multi_select = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "MultiSelectSkillFilter"
    )
    team_timeline = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "TeamSkillTimeline"
    )

    core_text = "".join(lines[: replay_worker.lineno - 1])
    core_text += '\n\n__all__ = tuple(name for name in globals() if not name.startswith("__"))\n'
    _write(UI_DIR / "live_dps_page_core.py", core_text)

    curves_text = "from __future__ import annotations\n\nfrom .live_dps_page_core import *\n\n"
    curves_text += "".join(lines[replay_worker.lineno - 1 : player_buff_curve.end_lineno])
    curves_text += '\n\n__all__ = tuple(name for name in globals() if not name.startswith("__"))\n'
    _write(UI_DIR / "live_dps_page_curves.py", curves_text)

    timeline_prefix_start = player_buff_curve.end_lineno
    timeline_text = "from __future__ import annotations\n\nfrom .live_dps_page_core import *\nfrom .live_dps_page_curves import *\n\n"
    timeline_text += "".join(lines[timeline_prefix_start : team_timeline.end_lineno])
    timeline_text += '\n\n__all__ = tuple(name for name in globals() if not name.startswith("__"))\n'
    _write(UI_DIR / "live_dps_timeline_widgets.py", timeline_text)

    manifest: dict[str, object] = {
        "source": str(SOURCE.relative_to(ROOT)),
        "source_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        "modules": {},
    }
    common = (
        "from __future__ import annotations\n\n"
        "from .live_dps_page_core import *\n"
        "from .live_dps_page_curves import *\n"
        "from .live_dps_timeline_widgets import *\n\n"
    )
    for filename, class_name, names in METHOD_GROUPS:
        body = "\n\n".join(_source_segment(lines, methods[name]) for name in names)
        indented = "\n".join(("    " + line if line else "") for line in body.splitlines())
        module_text = f"{common}class {class_name}:\n{indented}\n"
        _write(UI_DIR / filename, module_text)
        manifest["modules"][filename] = {
            "class": class_name,
            "methods": list(names),
            "method_ast_sha256": {
                name: hashlib.sha256(
                    ast.dump(methods[name], include_attributes=False).encode("utf-8")
                ).hexdigest()
                for name in names
            },
        }

    mixin_imports = "\n".join(
        f"from .{Path(filename).stem} import {class_name}"
        for filename, class_name, _ in METHOD_GROUPS
    )
    mixin_names = ",\n    ".join(class_name for _, class_name, _ in METHOD_GROUPS)
    facade = f'''from __future__ import annotations

# Compatibility facade: external callers continue importing every public and
# historical helper from dragon_nest_builder.ui.live_dps_page.
from .live_dps_page_core import *
from .live_dps_page_curves import *
from .live_dps_timeline_widgets import *
{mixin_imports}


class LiveDpsPage(
    {mixin_names},
    TheoryPlanBuilderMixin,
    QWidget,
):
    UI_REFRESH_INTERVAL_SECONDS = 2.0
    CAPTURE_COMPLEX_REFRESH_INTERVAL_SECONDS = 3.0


# Preserve the old module-level patching contract.  Code moved into mixins
# keeps its own module globals, therefore assignments made through the legacy
# ``ui.live_dps_page`` import must be mirrored to those implementation modules.
import sys as _compat_sys
import types as _compat_types
from . import live_dps_page_boss_statistics as _split_boss_statistics
from . import live_dps_page_capture as _split_capture
from . import live_dps_page_layout as _split_layout
from . import live_dps_page_party as _split_party
from . import live_dps_page_scoring as _split_scoring
from . import live_dps_page_views as _split_views


class _LiveDpsPageCompatibilityModule(_compat_types.ModuleType):
    _split_targets = (
        _split_boss_statistics,
        _split_capture,
        _split_layout,
        _split_party,
        _split_scoring,
        _split_views,
    )

    def __setattr__(self, name: str, value: object) -> None:
        super().__setattr__(name, value)
        for target in self._split_targets:
            if hasattr(target, name):
                setattr(target, name, value)


_compat_sys.modules[__name__].__class__ = _LiveDpsPageCompatibilityModule
'''
    _write(SOURCE, facade)
    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
