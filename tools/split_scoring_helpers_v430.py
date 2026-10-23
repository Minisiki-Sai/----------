from __future__ import annotations

import ast
import hashlib
import json
import textwrap
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "dragon_nest_builder/live_dps/scoring.py"
MANIFEST = ROOT / "docs/V430_SCORING_HELPER_SPLIT_MANIFEST.json"
MARKER = "V430_SCORING_HELPER_SPLIT_APPLIED"
GROUPS = (
    (
        "score_survival.py",
        "_bind_score_survival",
        "_excluded_from_score_for_low_damage",
        "_apply_survival_penalty_cap",
    ),
    (
        "score_intervals.py",
        "_bind_score_intervals",
        "_support_completion_is_scored",
        "_builtin_skill_builds",
    ),
)


@dataclass(frozen=True)
class Replacement:
    start: int
    end: int
    rows: tuple[str, ...] = ()


def ast_hash(node: ast.AST) -> str:
    return hashlib.sha256(
        ast.dump(node, annotate_fields=True, include_attributes=False).encode("utf-8")
    ).hexdigest()


def main() -> None:
    original = SOURCE.read_text(encoding="utf-8")
    if MARKER in original:
        raise SystemExit("scoring helper split already applied")
    lines = original.splitlines(keepends=True)
    tree = ast.parse(original)
    functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    entries: list[dict[str, object]] = []
    replacements: list[Replacement] = []
    binders: list[str] = []
    for filename, binder, first, last in GROUPS:
        start_index = next(index for index, node in enumerate(functions) if node.name == first)
        end_index = next(index for index, node in enumerate(functions) if node.name == last)
        selected = functions[start_index : end_index + 1]
        blocks: list[str] = []
        for node in selected:
            node_start = min(
                [node.lineno]
                + [decorator.lineno for decorator in getattr(node, "decorator_list", ())]
            )
            blocks.append(textwrap.dedent("".join(lines[node_start - 1 : node.end_lineno])))
        extra_import = (
            'from functools import lru_cache\n\n'
            '_LIMITED_RECORDED_TEAM_BUFF_KEYS = frozenset({"九天之舞2阶"})\n\n'
            if filename == "score_intervals.py"
            else ""
        )
        module_text = f'''from __future__ import annotations

{extra_import}
def bind_host_globals(namespace: dict[str, object]) -> None:
    globals().update(namespace)


''' + "\n\n".join(block.rstrip() for block in blocks) + "\n"
        target = SOURCE.with_name(filename)
        target.write_text(module_text, encoding="utf-8", newline="\n")
        symbols = [node.name for node in selected]
        stem = target.stem
        imports = (
            f"from .{stem} import bind_host_globals as {binder}\n",
            f"from .{stem} import (\n",
            *(f"    {name},\n" for name in symbols),
            ")\n",
        )
        first_node = selected[0]
        first_start = min(
            [first_node.lineno]
            + [decorator.lineno for decorator in getattr(first_node, "decorator_list", ())]
        )
        replacements.append(
            Replacement(first_start, selected[-1].end_lineno, imports)
        )
        binders.append(binder)
        entries.append(
            {
                "module": f"dragon_nest_builder/live_dps/{filename}",
                "symbols": symbols,
                "functionAstSha256": {node.name: ast_hash(node) for node in selected},
            }
        )

    rewritten = lines[:]
    for operation in sorted(replacements, key=lambda row: row.start, reverse=True):
        rewritten[operation.start - 1 : operation.end] = list(operation.rows)
    result = "".join(rewritten).rstrip() + "\n\n"
    result += f"# {MARKER}\n"
    result += "\n".join(f"{binder}(globals())" for binder in binders) + "\n"
    SOURCE.write_text(result, encoding="utf-8", newline="\n")
    MANIFEST.write_text(
        json.dumps(
            {
                "baseline": "v4.25",
                "target": "v4.30",
                "algorithmChanges": False,
                "source": "dragon_nest_builder/live_dps/scoring.py",
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
