"""Conservative dead-CSS filter for frontend/src/styles.css.

A rule is only dropped when EVERY class token in its selector contains a hyphen
and appears nowhere in the remaining frontend source. Generic single-word
tokens (active, selected, is-*, tone-*) are always kept so dynamic class
bindings keep working.
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS = os.path.join(ROOT, "src", "styles.css")


def load_corpus():
    chunks = []
    for base, _dirs, files in os.walk(os.path.join(ROOT, "src")):
        for name in files:
            if name.endswith((".vue", ".ts", ".css")) and name != "styles.css":
                with io.open(os.path.join(base, name), encoding="utf-8", errors="ignore") as fh:
                    chunks.append(fh.read())
    with io.open(os.path.join(ROOT, "index.html"), encoding="utf-8", errors="ignore") as fh:
        chunks.append(fh.read())
    return "\n".join(chunks)


def split_blocks(text):
    """Yield ('rule'|'atblock', prelude, body_or_none, raw) at top level."""
    i, n = 0, len(text)
    while i < n:
        brace = text.find("{", i)
        if brace < 0:
            yield ("raw", "", None, text[i:])
            return
        prelude = text[i:brace]
        depth, j = 1, brace + 1
        while j < n and depth:
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
            j += 1
        body = text[brace + 1 : j - 1]
        is_at = prelude.strip().startswith("@")
        if is_at and ("{" in body):
            yield ("atblock", prelude, body, None)
        else:
            yield ("rule", prelude, body, prelude + text[brace:j])
        i = j


def class_tokens(selector):
    bare = re.sub(r"\{[^{}]*\}", "", selector)
    return set(re.findall(r"\.(-?[A-Za-z_][\w-]*)", bare))


def dynamic_affixes(corpus):
    """Class-name fragments that are concatenated at runtime, e.g.
    ``missing-${row.mode}`` or ``'extra-tone-' + value``."""
    prefixes = set(re.findall(r"([\w-]+?)\$\{", corpus))
    prefixes |= set(re.findall(r"['\"`]([\w-]+)['\"`]\s*\+", corpus))
    suffixes = set(re.findall(r"\+\s*['\"`]([\w-]+)['\"`]", corpus))
    return prefixes, suffixes


def is_dead(selector, corpus, prefix_cache):
    tokens = class_tokens(selector)
    tokens = {t for t in tokens if not re.match(r"^\d", t)}
    if not tokens:
        return False
    prefixes, suffixes = prefix_cache
    for token in tokens:
        if "-" not in token:
            return False
        if token in corpus:
            return False
        if any(token.startswith(p) for p in prefixes if p.endswith("-")):
            return False
        if any(token.endswith(s) for s in suffixes if s.startswith("-")):
            return False
    return True


def main(apply):
    corpus = load_corpus()
    prefix_cache = dynamic_affixes(corpus)
    with io.open(CSS, encoding="utf-8", newline="") as fh:
        css = fh.read()
    out = []
    removed_rules = 0
    removed_chars = 0
    for kind, prelude, body, raw in split_blocks(css):
        if kind == "atblock":
            inner_out = []
            for k2, p2, b2, r2 in split_blocks(body):
                if k2 == "rule" and is_dead(p2, corpus, prefix_cache):
                    removed_rules += 1
                    removed_chars += len(r2 or "")
                    continue
                if k2 in ("rule", "atblock"):
                    inner_out.append(f"{p2}{{{b2}}}")
                else:
                    inner_out.append(r2)
            if not "".join(inner_out).strip():
                removed_chars += len(body)
                continue
            out.append(f"{prelude}{{{''.join(inner_out)}}}")
        elif kind == "rule":
            if is_dead(prelude, corpus, prefix_cache):
                removed_rules += 1
                removed_chars += len(raw or "")
                continue
            out.append(raw or f"{prelude}{{{body}}}")
        else:
            out.append(raw)
    result = "".join(out)
    # collapse the blank gaps left behind by removed rules
    result = re.sub(r"\n{3,}", "\n\n", result)
    print(f"removed rules: {removed_rules}, chars: {removed_chars}, "
          f"size {len(css)} -> {len(result)}")
    if apply:
        with io.open(CSS, "w", encoding="utf-8", newline="") as fh:
            fh.write(result)
        print("written")


if __name__ == "__main__":
    main("--apply" in sys.argv)
