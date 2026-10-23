from __future__ import annotations

import ast
import hashlib
import json
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "dragon_nest_builder/live_dps/score_support.py"
MANIFEST = ROOT / "docs/V430_SCORE_SUPPORT_SPLIT_MANIFEST.json"
GROUPS = (
    (
        "score_support_catalog.py",
        "ScoreSupportCatalogMixin",
        "_support_expectations",
        "_team_attack_buff_allocation",
    ),
    (
        "score_support_allocation.py",
        "ScoreSupportAllocationMixin",
        "_support_effect_relevance",
        "_freeze_damage_weighted_contributions",
    ),
    (
        "score_support_counterfactual.py",
        "ScoreSupportCounterfactualMixin",
        "_build_support_counterfactual_context",
        "_support_scores",
    ),
)


def ast_hash(node: ast.AST) -> str:
    return hashlib.sha256(
        ast.dump(node, annotate_fields=True, include_attributes=False).encode("utf-8")
    ).hexdigest()


def main() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    if "ScoreSupportCatalogMixin" in source:
        raise SystemExit("score support split already applied")
    lines = source.splitlines(keepends=True)
    tree = ast.parse(source)
    owner = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ScoreSupportMixin"
    )
    methods = [
        node
        for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    by_name = {node.name: node for node in methods}
    entries: list[dict[str, object]] = []
    covered: set[str] = set()
    imports: list[str] = []
    bases: list[str] = []
    binders: list[str] = []
    for filename, mixin, first, last in GROUPS:
        start = next(index for index, node in enumerate(methods) if node.name == first)
        end = next(index for index, node in enumerate(methods) if node.name == last)
        selected = methods[start : end + 1]
        covered.update(node.name for node in selected)
        blocks = []
        for node in selected:
            node_start = min(
                [node.lineno]
                + [decorator.lineno for decorator in getattr(node, "decorator_list", ())]
            )
            blocks.append(textwrap.dedent("".join(lines[node_start - 1 : node.end_lineno])))
        body = "\n\n".join(block.rstrip() for block in blocks)
        indented = "\n".join("    " + line if line else "" for line in body.splitlines())
        extra_import = (
            "from functools import lru_cache\n\n"
            if filename in {"score_support_catalog.py", "score_support_allocation.py"}
            else ""
        )
        module = f'''from __future__ import annotations

{extra_import}
def bind_host_globals(namespace: dict[str, object]) -> None:
    globals().update(namespace)


class {mixin}:
{indented}
'''
        path = SOURCE.with_name(filename)
        path.write_text(module, encoding="utf-8", newline="\n")
        stem = path.stem
        binder = f"_bind_{mixin}"
        imports.append(
            f"from .{stem} import {mixin}, bind_host_globals as {binder}"
        )
        bases.append(mixin)
        binders.append(binder)
        entries.append(
            {
                "module": f"dragon_nest_builder/live_dps/{filename}",
                "mixin": mixin,
                "symbols": [node.name for node in selected],
                "methodAstSha256": {node.name: ast_hash(node) for node in selected},
            }
        )
    if covered != set(by_name):
        raise RuntimeError(f"unassigned methods: {sorted(set(by_name) - covered)}")

    facade = "from __future__ import annotations\n\n"
    facade += "\n".join(imports)
    facade += "\n\n\nclass ScoreSupportMixin(\n"
    facade += "\n".join(f"    {base}," for base in bases)
    facade += "\n):\n    pass\n\n\ndef bind_host_globals(host_globals: dict[str, object]) -> None:\n"
    facade += "\n".join(f"    {binder}(host_globals)" for binder in binders)
    facade += "\n\n\n# Extracted from scoring.py; algorithm bodies are unchanged.\n"
    SOURCE.write_text(facade, encoding="utf-8", newline="\n")
    MANIFEST.write_text(
        json.dumps(
            {
                "baseline": "v4.25",
                "target": "v4.30",
                "algorithmChanges": False,
                "source": "dragon_nest_builder/live_dps/scoring.py",
                "sourceOwner": "TeamScoreEngine",
                "modules": entries,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
