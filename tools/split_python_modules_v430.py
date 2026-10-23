from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "V430_PYTHON_SPLIT_MANIFEST.json"
MARKER = "V430_SPLIT_MODULES_APPLIED"


@dataclass(frozen=True)
class Operation:
    start: int
    end: int
    replacement: tuple[str, ...] = ()


def _read(path: Path) -> tuple[str, list[str]]:
    text = path.read_text(encoding="utf-8-sig")
    return text, text.splitlines(keepends=True)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _node_start(node: ast.AST) -> int:
    decorators = getattr(node, "decorator_list", ())
    return min([node.lineno, *(item.lineno for item in decorators)])


def _block(lines: list[str], node: ast.AST) -> str:
    return "".join(lines[_node_start(node) - 1 : node.end_lineno]).rstrip() + "\n"


def _rewrite(lines: list[str], operations: Iterable[Operation]) -> str:
    result = list(lines)
    for operation in sorted(operations, key=lambda item: item.start, reverse=True):
        result[operation.start - 1 : operation.end] = list(operation.replacement)
    return "".join(result)


def _class(tree: ast.Module, name: str) -> ast.ClassDef:
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == name
    )


def _methods_between(
    class_node: ast.ClassDef, first: str, last: str
) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    methods = [
        node
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    names = [node.name for node in methods]
    start = names.index(first)
    end = names.index(last)
    if end < start:
        raise ValueError(f"invalid range {first}..{last}")
    return methods[start : end + 1]


def _module_text(
    *,
    title: str,
    class_name: str,
    method_blocks: list[str],
    extra_header: str = "",
    preserve_host_file: bool = False,
) -> str:
    body = "\n".join(block.rstrip() for block in method_blocks)
    protected = repr({"bind_host_globals", class_name})
    host_file_binding = (
        "    source_file = host_globals.get('__file__')\n"
        "    if isinstance(source_file, str):\n"
        "        globals()['__file__'] = source_file\n"
        if preserve_host_file
        else ""
    )
    return (
        "from __future__ import annotations\n\n"
        "from functools import lru_cache\n"
        f"{extra_header}"
        "\n"
        f"class {class_name}:\n"
        f"{body if body else '    pass'}\n\n\n"
        "def bind_host_globals(host_globals: dict[str, object]) -> None:\n"
        f"    protected = {protected}\n"
        "    for name, value in host_globals.items():\n"
        "        if name.startswith('__') or name in protected:\n"
        "            continue\n"
        "        globals()[name] = value\n"
        f"{host_file_binding}"
        f"\n\n# {title}\n"
    )


def _plain_module_text(*, title: str, blocks: list[str], names: list[str]) -> str:
    body = "\n\n".join(block.rstrip() for block in blocks)
    protected = repr({"bind_host_globals", *names})
    return (
        "from __future__ import annotations\n\n"
        "import codecs\n"
        "import copy\n"
        "import ctypes\n"
        "import dataclasses\n"
        "import hashlib\n"
        "import itertools\n"
        "import json\n"
        "import math\n"
        "import os\n"
        "import queue\n"
        "import re\n"
        "import shutil\n"
        "import socket\n"
        "import sqlite3\n"
        "import struct\n"
        "import threading\n"
        "import time\n"
        "import traceback\n"
        "import uuid\n"
        "from bisect import bisect_left, bisect_right, insort\n"
        "from collections import Counter, defaultdict, deque\n"
        "from dataclasses import dataclass, field, replace\n"
        "from datetime import datetime\n"
        "from functools import lru_cache\n"
        "from pathlib import Path\n"
        "from typing import Any, Callable, Iterable, Protocol\n\n"
        f"{body}\n\n\n"
        "def bind_host_globals(host_globals: dict[str, object]) -> None:\n"
        f"    protected = {protected}\n"
        "    for name, value in host_globals.items():\n"
        "        if name.startswith('__') or name in protected:\n"
        "            continue\n"
        "        globals()[name] = value\n"
        f"\n\n# {title}\n"
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


def _split_class_methods(
    *,
    source: Path,
    class_name: str,
    groups: list[tuple[str, str, str, str]],
    keep_methods: set[str],
    manifest: dict[str, object],
    extra_headers: dict[str, str] | None = None,
    preserve_host_file_modules: set[str] | None = None,
) -> None:
    original, lines = _read(source)
    tree = ast.parse(original)
    class_node = _class(tree, class_name)
    operations: list[Operation] = []
    mixin_names: list[str] = []
    imports: list[str] = []
    bind_calls: list[str] = []
    moved: list[dict[str, object]] = []
    extra_headers = extra_headers or {}
    preserve_host_file_modules = preserve_host_file_modules or set()

    for relative_module, mixin_name, first, last in groups:
        methods = _methods_between(class_node, first, last)
        methods = [node for node in methods if node.name not in keep_methods]
        module_path = source.parent / f"{relative_module}.py"
        module_text = _module_text(
            title=f"Extracted from {source.name}; algorithm bodies are unchanged.",
            class_name=mixin_name,
            method_blocks=[_block(lines, node) for node in methods],
            extra_header=extra_headers.get(relative_module, ""),
            preserve_host_file=relative_module in preserve_host_file_modules,
        )
        _write(module_path, module_text)
        for node in methods:
            operations.append(Operation(_node_start(node), node.end_lineno))
        module_import = relative_module.replace("/", ".")
        alias = f"_bind_{mixin_name}"
        imports.append(
            f"from .{module_import} import {mixin_name}, bind_host_globals as {alias}\n"
        )
        bind_calls.append(f"{alias}(globals())\n")
        mixin_names.append(mixin_name)
        moved.append(
            {
                "module": str(module_path.relative_to(ROOT)).replace("\\", "/"),
                "mixin": mixin_name,
                "symbols": [node.name for node in methods],
                "sourceBodySha256": _sha("".join(_block(lines, node) for node in methods)),
            }
        )

    header = lines[class_node.lineno - 1]
    indent = header[: len(header) - len(header.lstrip())]
    moved_ids = {
        id(node)
        for _, _, first, last in groups
        for node in _methods_between(class_node, first, last)
    }
    remaining_body = [node for node in class_node.body if id(node) not in moved_ids]
    replacement_header = f"{indent}class {class_name}({', '.join(mixin_names)}):\n"
    if not remaining_body:
        replacement_header += f"{indent}    pass\n"
    operations.append(
        Operation(
            class_node.lineno,
            class_node.lineno,
            tuple([*imports, "\n", replacement_header]),
        )
    )
    rewritten = _rewrite(lines, operations)
    rewritten = rewritten.rstrip() + "\n\n# " + MARKER + "\n" + "".join(bind_calls)
    _write(source, rewritten)
    manifest[str(source.relative_to(ROOT)).replace("\\", "/")] = {
        "beforeSha256": _sha(original),
        "afterSha256": _sha(rewritten),
        "beforeLines": len(lines),
        "afterLines": len(rewritten.splitlines()),
        "moved": moved,
    }


def _split_top_level_nodes(
    *,
    source: Path,
    module_name: str,
    names: list[str],
    manifest: dict[str, object],
) -> None:
    original, lines = _read(source)
    tree = ast.parse(original)
    by_name = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    nodes = [by_name[name] for name in names]
    module_path = source.parent / f"{module_name}.py"
    module_text = _plain_module_text(
        title=f"Extracted from {source.name}; algorithm bodies are unchanged.",
        blocks=[_block(lines, node) for node in nodes],
        names=names,
    )
    _write(module_path, module_text)
    module_import = module_name.replace("/", ".")
    binder = f"_bind_{module_name.replace('/', '_')}"
    import_rows = [
        f"from .{module_import} import bind_host_globals as {binder}\n",
        f"from .{module_import} import (\n",
        *(f"    {name},\n" for name in names),
        ")\n\n",
    ]
    operations = [Operation(_node_start(node), node.end_lineno) for node in nodes]
    first = min(_node_start(node) for node in nodes)
    first_operation = next(item for item in operations if item.start == first)
    operations.remove(first_operation)
    operations.append(
        Operation(first_operation.start, first_operation.end, tuple(import_rows))
    )
    rewritten = _rewrite(lines, operations)
    rewritten = rewritten.rstrip() + f"\n\n{binder}(globals())\n"
    _write(source, rewritten)
    entry = manifest.setdefault(
        str(source.relative_to(ROOT)).replace("\\", "/"),
        {
            "beforeSha256": _sha(original),
            "beforeLines": len(lines),
            "moved": [],
        },
    )
    entry["afterSha256"] = _sha(rewritten)
    entry["afterLines"] = len(rewritten.splitlines())
    entry["moved"].append(
        {
            "module": str(module_path.relative_to(ROOT)).replace("\\", "/"),
            "symbols": names,
            "sourceBodySha256": _sha("".join(_block(lines, node) for node in nodes)),
        }
    )


def _split_scoring(manifest: dict[str, object]) -> None:
    _split_class_methods(
        source=ROOT / "dragon_nest_builder/live_dps/scoring.py",
        class_name="TeamScoreEngine",
        keep_methods={"__init__", "score_boss_name", "set_boss_profile", "score"},
        groups=[
            ("score_engine", "ScoreEngineMixin", "_observed_cast_timings", "_score_player"),
            ("score_support_context", "ScoreSupportContextMixin", "_counterfactual_support_weight", "_apply_counterfactual_support_weights"),
            ("score_profiles", "ScoreProfilesMixin", "_learned_skills", "_level_power"),
            ("score_glyphs", "ScoreGlyphsMixin", "_apply_best_glyphs", "_glyph_display_rows"),
            ("score_rotation", "ScoreRotationMixin", "_is_dot_damage_event", "_yao_meditation_coverage"),
            ("score_support", "ScoreSupportMixin", "_support_expectations", "_support_scores"),
            ("score_role_duties", "ScoreRoleDutiesMixin", "_select_theory_support_intervals", "_tank_completion"),
        ],
        manifest=manifest,
    )


def _split_native_core(manifest: dict[str, object]) -> None:
    source = ROOT / "dragon_nest_builder/v410_native_core.py"
    _split_top_level_nodes(
        source=source,
        module_name="native/live_capture_service",
        names=["NativeLiveCapture", "NativeJobCancelled", "NativeJobQueue"],
        manifest=manifest,
    )
    _split_class_methods(
        source=source,
        class_name="NativeSimulatorCore",
        keep_methods={"__init__", "shutdown", "mechanism_templates"},
        groups=[
            ("native/custom_team_service", "NativeCustomTeamMixin", "_payload_team_members", "_cached_personal_timeline"),
            ("native/overlay_service", "NativeOverlayMixin", "capture_status", "clear_capture"),
            ("native/profile_sync_service", "NativeProfileSyncMixin", "_profile_fields", "cancel_profile_sync"),
            ("native/build_service", "NativeBuildMixin", "_skill_defaults", "optimize_skill_glyphs"),
            ("native/team_service", "NativeTeamMixin", "_auto_glyph_cache_key", "optimize_team"),
            ("native/replay_score_service", "NativeReplayScoreMixin", "load_replay", "score_detail"),
        ],
        manifest=manifest,
        extra_headers={
            "native/build_service": "\nNONE_TEXT = '无'\n",
        },
    )
    init_path = ROOT / "dragon_nest_builder/native/__init__.py"
    if not init_path.exists():
        _write(init_path, '"""Native v4.30 service mixins."""\n')


def _split_skill_timeline(manifest: dict[str, object]) -> None:
    _split_class_methods(
        source=ROOT / "dragon_nest_builder/mixins/skill_timeline_engine.py",
        class_name="SkillTimelineEngineMixin",
        keep_methods=set(),
        groups=[
            ("skill_timeline_schedule", "SkillTimelineScheduleMixin", "_skill_burst_blocked_ranges", "_skill_burst_next_opening_support"),
            ("skill_timeline_state", "SkillTimelineStateMixin", "_skill_burst_freeze_source", "_skill_burst_holy_ground_recent"),
            ("skill_timeline_effects", "SkillTimelineEffectsMixin", "_skill_burst_resist_attr_for_element", "_skill_burst_periodic_realtime_debuff_multiplier"),
            ("skill_timeline_output", "SkillTimelineOutputMixin", "_skill_burst_summon_duration", "_skill_burst_panel_multiplier_for_attrs"),
        ],
        manifest=manifest,
    )


def _split_sources(manifest: dict[str, object]) -> None:
    source = ROOT / "dragon_nest_builder/live_dps/sources.py"
    _split_top_level_nodes(
        source=source,
        module_name="replay_normalization",
        names=[
            "_live_target_identity", "_canonical_live_skill_id", "_canonicalize_live_message",
            "_supplement_boss_statuses_from_casts", "_supplement_player_buffs_from_casts",
            "_backfill_preexisting_maintained_player_buffs", "_canonicalize_replay_actor_context",
            "_ReplayPlayerSegment", "_is_generic_replay_actor_name",
            "_replay_skill_signature_similarity", "_replay_rejoined_player_aliases",
            "_merge_rejoined_replay_players_once", "_merge_rejoined_replay_players",
            "_backfill_permanent_skill_object_history",
        ],
        manifest=manifest,
    )
    _split_top_level_nodes(
        source=source,
        module_name="protocol_catalogs",
        names=[
            "_load_monster_templates", "_load_boss_action_catalog", "_load_boss_skill_catalog",
            "_load_player_buff_clear_boss_skill_ids", "_load_skill_catalog", "_load_damage_skill_ids",
            "_load_status_skill_catalog", "_load_team_buff_skill_names", "_load_status_effect_catalog",
            "_load_periodic_damage_catalog", "_load_skill_profiles", "_load_profile_jobs",
            "_load_inspect_job_codes", "_load_profile_lineages",
        ],
        manifest=manifest,
    )
    _split_top_level_nodes(
        source=source,
        module_name="capture_sources",
        names=[
            "CombatEventSource", "JsonlReplaySource", "V576PcapReplaySource",
            "V576DiagnosticReplaySource", "V576PktmonTextStreamSource",
            "auto_capture_confirmation_reason", "V576AutoCaptureProbe",
            "V576NpcapSource", "V576MemorySource",
        ],
        manifest=manifest,
    )
    _split_class_methods(
        source=source,
        class_name="_V576PacketEventDecoder",
        keep_methods={"__init__", "feed", "feed_events"},
        groups=[
            ("v576/actor_state", "V576ActorStateMixin", "_reject_extreme_damage_outlier", "_observe_party_member"),
            ("v576/profile_decoder", "V576ProfileDecoderMixin", "_observe_profile_probe", "_observe_card_profile_update"),
            ("v576/combat_decoder", "V576CombatDecoderMixin", "_healing_source_context", "_is_inactive_player_special_attack"),
            ("v576/status_decoder", "V576StatusDecoderMixin", "_packet_percent", "_player_health_events"),
        ],
        manifest=manifest,
        preserve_host_file_modules={"v576/profile_decoder"},
    )
    init_path = ROOT / "dragon_nest_builder/live_dps/v576/__init__.py"
    if not init_path.exists():
        _write(init_path, '"""V576 decoder responsibility mixins."""\n')
    compatibility_facade = r'''

# Keep the historical ``live_dps.sources`` module a true compatibility
# facade.  Tests and third-party integrations have always patched decoder or
# capture dependencies through this module.  Methods moved to the split
# modules resolve globals in their defining module, so forward later facade
# assignments to every split implementation as well.
import sys as _compat_sys
import types as _compat_types
from . import capture_sources as _split_capture_sources
from . import protocol_catalogs as _split_protocol_catalogs
from . import replay_normalization as _split_replay_normalization
from .v576 import actor_state as _split_actor_state
from .v576 import combat_decoder as _split_combat_decoder
from .v576 import profile_decoder as _split_profile_decoder
from .v576 import status_decoder as _split_status_decoder


class _SourcesCompatibilityModule(_compat_types.ModuleType):
    _split_targets = (
        _split_capture_sources,
        _split_protocol_catalogs,
        _split_replay_normalization,
        _split_actor_state,
        _split_combat_decoder,
        _split_profile_decoder,
        _split_status_decoder,
    )

    def __setattr__(self, name: str, value: object) -> None:
        super().__setattr__(name, value)
        for target in self._split_targets:
            if hasattr(target, name):
                setattr(target, name, value)


_compat_sys.modules[__name__].__class__ = _SourcesCompatibilityModule
'''
    source.write_text(
        source.read_text(encoding="utf-8").rstrip() + compatibility_facade + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main() -> int:
    scoring = ROOT / "dragon_nest_builder/live_dps/scoring.py"
    if MARKER in scoring.read_text(encoding="utf-8-sig"):
        raise SystemExit("v4.30 Python split has already been applied")
    manifest: dict[str, object] = {
        "schema": 1,
        "baseline": "v4.25",
        "target": "v4.30",
        "algorithmChanges": False,
        "files": {},
    }
    files = manifest["files"]
    assert isinstance(files, dict)
    _split_scoring(files)
    _split_native_core(files)
    _split_skill_timeline(files)
    _split_sources(files)
    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(MANIFEST)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
