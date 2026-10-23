from __future__ import annotations

import ast
import hashlib
import json
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "dragon_nest_builder/live_dps/sources.py"
TARGET = ROOT / "dragon_nest_builder/live_dps/v576/core_decoder.py"
MANIFEST = ROOT / "docs/V430_V576_CORE_SPLIT_MANIFEST.json"
MARKER = "V430_V576_CORE_SPLIT_APPLIED"


def ast_hash(node: ast.AST) -> str:
    return hashlib.sha256(
        ast.dump(node, annotate_fields=True, include_attributes=False).encode("utf-8")
    ).hexdigest()


def main() -> None:
    original = SOURCE.read_text(encoding="utf-8")
    if MARKER in original:
        raise SystemExit("V576 core split already applied")
    lines = original.splitlines(keepends=True)
    tree = ast.parse(original)
    decoder = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "_V576PacketEventDecoder"
    )
    methods = [
        node
        for node in decoder.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    if [node.name for node in methods] != ["__init__", "feed", "feed_events"]:
        raise RuntimeError(f"unexpected core methods: {[node.name for node in methods]}")

    blocks: list[str] = []
    for node in methods:
        start = min(
            [node.lineno]
            + [decorator.lineno for decorator in getattr(node, "decorator_list", ())]
        )
        blocks.append(textwrap.dedent("".join(lines[start - 1 : node.end_lineno])))
    body = "\n\n".join(block.rstrip() for block in blocks)
    indented = "\n".join("    " + line if line else "" for line in body.splitlines())
    target_text = f'''from __future__ import annotations


def bind_host_globals(namespace: dict[str, object]) -> None:
    globals().update(namespace)


class V576DecoderCoreMixin:
{indented}
'''
    TARGET.write_text(target_text, encoding="utf-8", newline="\n")

    replacement = '''from .v576.core_decoder import (
    V576DecoderCoreMixin,
    bind_host_globals as _bind_V576DecoderCoreMixin,
)


class _V576PacketEventDecoder(
    V576DecoderCoreMixin,
    V576ActorStateMixin,
    V576ProfileDecoderMixin,
    V576CombatDecoderMixin,
    V576StatusDecoderMixin,
):
    pass


# V430_V576_CORE_SPLIT_APPLIED
'''
    rewritten = (
        "".join(lines[: decoder.lineno - 1])
        + replacement
        + "".join(lines[decoder.end_lineno :])
    )
    bind_anchor = "_bind_V576StatusDecoderMixin(globals())\n"
    if bind_anchor not in rewritten:
        raise RuntimeError("status binder anchor not found")
    rewritten = rewritten.replace(
        bind_anchor,
        bind_anchor + "_bind_V576DecoderCoreMixin(globals())\n",
        1,
    )
    import_anchor = "from .v576 import actor_state as _split_actor_state\n"
    if import_anchor not in rewritten:
        raise RuntimeError("compat import anchor not found")
    rewritten = rewritten.replace(
        import_anchor,
        "from .v576 import core_decoder as _split_core_decoder\n" + import_anchor,
        1,
    )
    target_anchor = "        _split_actor_state,\n"
    if target_anchor not in rewritten:
        raise RuntimeError("compat target anchor not found")
    rewritten = rewritten.replace(
        target_anchor,
        "        _split_core_decoder,\n" + target_anchor,
        1,
    )
    SOURCE.write_text(rewritten, encoding="utf-8", newline="\n")
    MANIFEST.write_text(
        json.dumps(
            {
                "baseline": "v4.25",
                "target": "v4.30",
                "algorithmChanges": False,
                "source": "dragon_nest_builder/live_dps/sources.py",
                "module": "dragon_nest_builder/live_dps/v576/core_decoder.py",
                "mixin": "V576DecoderCoreMixin",
                "symbols": [node.name for node in methods],
                "methodAstSha256": {node.name: ast_hash(node) for node in methods},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
