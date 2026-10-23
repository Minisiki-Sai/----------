from __future__ import annotations

import argparse
import ast
import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class SymbolRef:
    file: Path
    symbol: str
    owner: str = ""


@lru_cache(maxsize=None)
def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def top_level(tree: ast.Module, name: str) -> ast.AST | None:
    return next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name == name
        ),
        None,
    )


def owned(tree: ast.Module, owner: str, name: str) -> ast.AST | None:
    parent = top_level(tree, owner)
    if not isinstance(parent, ast.ClassDef):
        return None
    return next(
        (
            node
            for node in parent.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ),
        None,
    )


def node_for(ref: SymbolRef) -> ast.AST | None:
    tree = parse(ref.file)
    return owned(tree, ref.owner, ref.symbol) if ref.owner else top_level(tree, ref.symbol)


def digest(node: ast.AST) -> str:
    value = ast.dump(node, annotate_fields=True, include_attributes=False)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def compare(before: SymbolRef, after: SymbolRef) -> dict[str, object]:
    before_node = node_for(before)
    after_node = node_for(after)
    before_hash = digest(before_node) if before_node is not None else ""
    after_hash = digest(after_node) if after_node is not None else ""
    return {
        "symbol": before.symbol,
        "before": f"{before.file}:{before.owner or '<module>'}",
        "after": f"{after.file}:{after.owner or '<module>'}",
        "beforeFound": before_node is not None,
        "afterFound": after_node is not None,
        "beforeAstSha256": before_hash,
        "afterAstSha256": after_hash,
        "equal": bool(before_hash) and before_hash == after_hash,
    }


def original_owner(relative: str) -> str:
    if relative.endswith("live_dps/scoring.py"):
        return "TeamScoreEngine"
    if relative.endswith("v410_native_core.py"):
        return "NativeSimulatorCore"
    if relative.endswith("mixins/skill_timeline_engine.py"):
        return "SkillTimelineEngineMixin"
    if relative.endswith("live_dps/sources.py"):
        return "_V576PacketEventDecoder"
    raise KeyError(relative)


def python_manifest_rows(
    baseline: Path,
    target: Path,
    manifest: dict[str, object],
    redirects: dict[str, tuple[str, str]] | None = None,
) -> Iterable[dict[str, object]]:
    redirects = redirects or {}
    files = manifest.get("files", {})
    assert isinstance(files, dict)
    for relative, entry_value in files.items():
        entry = entry_value
        assert isinstance(entry, dict)
        for moved_value in entry.get("moved", []):
            moved = moved_value
            assert isinstance(moved, dict)
            target_relative = str(moved["module"])
            mixin = str(moved.get("mixin", ""))
            for symbol_value in moved.get("symbols", []):
                symbol = str(symbol_value)
                before_owner = original_owner(str(relative)) if mixin else ""
                redirected = redirects.get(symbol)
                after_relative, after_owner = (
                    redirected if redirected is not None else (target_relative, mixin)
                )
                yield compare(
                    SymbolRef(baseline / str(relative), symbol, before_owner),
                    SymbolRef(target / after_relative, symbol, after_owner),
                )


def first_page_rows(
    baseline: Path, target: Path, manifest: dict[str, object]
) -> Iterable[dict[str, object]]:
    before_file = baseline / "dragon_nest_builder/ui/live_dps_page.py"
    modules = manifest.get("modules", {})
    assert isinstance(modules, dict)
    for module_name, entry_value in modules.items():
        entry = entry_value
        assert isinstance(entry, dict)
        target_file = target / "dragon_nest_builder/live_dps" / str(module_name)
        for symbol_value in entry.get("symbols", []):
            symbol = str(symbol_value)
            yield compare(
                SymbolRef(before_file, symbol),
                SymbolRef(target_file, symbol),
            )
        for symbol_value in entry.get("helpers", []):
            symbol = str(symbol_value)
            yield compare(
                SymbolRef(before_file, symbol),
                SymbolRef(target_file, symbol),
            )
        for symbol_value in entry.get("methods", []):
            symbol = str(symbol_value)
            yield compare(
                SymbolRef(before_file, symbol, "LiveDpsPage"),
                SymbolRef(target_file, symbol, "TheoryPlanBuilderMixin"),
            )


def page_layer_rows(
    baseline: Path, target: Path, manifest: dict[str, object]
) -> Iterable[dict[str, object]]:
    before_file = baseline / "dragon_nest_builder/ui/live_dps_page.py"
    modules = manifest.get("modules", {})
    assert isinstance(modules, dict)
    for module_name, entry_value in modules.items():
        entry = entry_value
        assert isinstance(entry, dict)
        owner = str(entry["class"])
        target_file = target / "dragon_nest_builder/ui" / str(module_name)
        for symbol_value in entry.get("methods", []):
            symbol = str(symbol_value)
            yield compare(
                SymbolRef(before_file, symbol, "LiveDpsPage"),
                SymbolRef(target_file, symbol, owner),
            )


def page_widget_rows(baseline: Path, target: Path) -> Iterable[dict[str, object]]:
    before_file = baseline / "dragon_nest_builder/ui/live_dps_page.py"
    before_tree = parse(before_file)
    names = {
        node.name
        for node in before_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name != "LiveDpsPage"
    }
    for relative in (
        "dragon_nest_builder/ui/live_dps_page_core.py",
        "dragon_nest_builder/ui/live_dps_page_curves.py",
        "dragon_nest_builder/ui/live_dps_timeline_widgets.py",
    ):
        target_file = target / relative
        tree = parse(target_file)
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if node.name not in names:
                continue
            yield compare(
                SymbolRef(before_file, node.name),
                SymbolRef(target_file, node.name),
            )


def v576_core_rows(
    baseline: Path, target: Path, manifest: dict[str, object]
) -> Iterable[dict[str, object]]:
    before_file = baseline / str(manifest["source"])
    after_file = target / str(manifest["module"])
    owner = str(manifest["mixin"])
    for symbol_value in manifest.get("symbols", []):
        symbol = str(symbol_value)
        yield compare(
            SymbolRef(before_file, symbol, "_V576PacketEventDecoder"),
            SymbolRef(after_file, symbol, owner),
        )


def score_support_rows(
    baseline: Path, target: Path, manifest: dict[str, object]
) -> Iterable[dict[str, object]]:
    before_file = baseline / str(manifest["source"])
    before_owner = str(manifest["sourceOwner"])
    for entry_value in manifest.get("modules", []):
        entry = entry_value
        assert isinstance(entry, dict)
        after_file = target / str(entry["module"])
        after_owner = str(entry["mixin"])
        for symbol_value in entry.get("symbols", []):
            symbol = str(symbol_value)
            yield compare(
                SymbolRef(before_file, symbol, before_owner),
                SymbolRef(after_file, symbol, after_owner),
            )


def scoring_helper_rows(
    baseline: Path, target: Path, manifest: dict[str, object]
) -> Iterable[dict[str, object]]:
    before_file = baseline / str(manifest["source"])
    for entry_value in manifest.get("modules", []):
        entry = entry_value
        assert isinstance(entry, dict)
        after_file = target / str(entry["module"])
        for symbol_value in entry.get("symbols", []):
            symbol = str(symbol_value)
            yield compare(
                SymbolRef(before_file, symbol),
                SymbolRef(after_file, symbol),
            )


def source_section(path: Path, marker: str) -> str:
    text = path.read_text(encoding="utf-8")
    index = text.find(marker)
    return text[index:] if index >= 0 else ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--target", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    baseline = args.baseline.resolve()
    target = args.target.resolve()

    python_manifest = json.loads(
        (target / "docs/V430_PYTHON_SPLIT_MANIFEST.json").read_text(encoding="utf-8")
    )
    first_manifest = json.loads(
        (target / "docs/V430_LIVE_PAGE_SPLIT_MANIFEST.json").read_text(encoding="utf-8")
    )
    layer_manifest = json.loads(
        (target / "docs/V430_LIVE_PAGE_LAYER_SPLIT_MANIFEST.json").read_text(encoding="utf-8")
    )
    v576_manifest = json.loads(
        (target / "docs/V430_V576_CORE_SPLIT_MANIFEST.json").read_text(encoding="utf-8")
    )
    score_support_manifest = json.loads(
        (target / "docs/V430_SCORE_SUPPORT_SPLIT_MANIFEST.json").read_text(encoding="utf-8")
    )
    scoring_helper_manifest = json.loads(
        (target / "docs/V430_SCORING_HELPER_SPLIT_MANIFEST.json").read_text(encoding="utf-8")
    )
    score_support_redirects = {
        str(symbol): (str(entry["module"]), str(entry["mixin"]))
        for entry_value in score_support_manifest.get("modules", [])
        for entry in (entry_value,)
        if isinstance(entry, dict)
        for symbol in entry.get("symbols", [])
    }
    groups = {
        "pythonCore": list(
            python_manifest_rows(
                baseline, target, python_manifest, score_support_redirects
            )
        ),
        "livePagePure": list(first_page_rows(baseline, target, first_manifest)),
        "livePageMixins": list(page_layer_rows(baseline, target, layer_manifest)),
        "livePageWidgets": list(page_widget_rows(baseline, target)),
        "v576Core": list(v576_core_rows(baseline, target, v576_manifest)),
        "scoreSupport": list(
            score_support_rows(baseline, target, score_support_manifest)
        ),
        "scoringHelpers": list(
            scoring_helper_rows(baseline, target, scoring_helper_manifest)
        ),
    }
    all_rows = [row for rows in groups.values() for row in rows]

    frontend_pairs = []
    for relative in (
        "frontend/src/views/LiveDpsView.vue",
        "frontend/src/components/BuffOverlay.vue",
    ):
        before_file = baseline / relative
        after_file = target / relative
        before_render = source_section(before_file, "<template>")
        after_render = source_section(after_file, "<template>")
        frontend_pairs.append(
            {
                "file": relative,
                "templateAndStyleEqual": before_render == after_render,
                "beforeSha256": hashlib.sha256(before_render.encode("utf-8")).hexdigest(),
                "afterSha256": hashlib.sha256(after_render.encode("utf-8")).hexdigest(),
            }
        )

    report = {
        "baseline": str(baseline),
        "target": str(target),
        "pythonMovedSymbolCount": len(all_rows),
        "pythonMovedSymbolsEqual": sum(bool(row["equal"]) for row in all_rows),
        "pythonAllEqual": all(bool(row["equal"]) for row in all_rows),
        "frontendTemplateAndStyleAllEqual": all(
            bool(row["templateAndStyleEqual"]) for row in frontend_pairs
        ),
        "frontend": frontend_pairs,
        "groups": groups,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "pythonMovedSymbolCount",
                    "pythonMovedSymbolsEqual",
                    "pythonAllEqual",
                    "frontendTemplateAndStyleAllEqual",
                )
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["pythonAllEqual"] and report["frontendTemplateAndStyleAllEqual"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
