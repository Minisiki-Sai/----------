"""Remove scoped-CSS rules from a Vue SFC whose classes no longer exist in the
remaining frontend source.  Used after whole overlay modules were deleted.

Usage: python scripts/prune_scoped_css.py <file.vue> [--apply]
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def corpus():
    """Every template/script in src, with <style> blocks stripped so that a rule
    is only kept when some component still uses its class."""
    chunks = []
    for base, _dirs, files in os.walk(os.path.join(ROOT, "src")):
        for name in files:
            path = os.path.join(base, name)
            if name.endswith((".vue", ".ts")):
                with io.open(path, encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
                if name.endswith(".vue"):
                    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.S)
                chunks.append(text)
    return "\n".join(chunks)


def dynamic_affixes(text):
    prefixes = set(re.findall(r"([\w-]+?)\$\{", text))
    prefixes |= set(re.findall(r"['\"`]([\w-]+)['\"`]\s*\+", text))
    suffixes = set(re.findall(r"\+\s*['\"`]([\w-]+)['\"`]", text))
    return prefixes, suffixes


def split_blocks(text):
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
        kind = "atblock" if prelude.strip().startswith("@") and "{" in body else "rule"
        yield (kind, prelude, body, prelude + text[brace:j])
        i = j


def is_dead(selector, text, affixes):
    tokens = set(re.findall(r"\.(-?[A-Za-z_][\w-]*)", selector))
    tokens = {t for t in tokens if not re.match(r"^\d", t)}
    if not tokens:
        return False
    prefixes, suffixes = affixes
    for token in tokens:
        if "-" not in token or token in text:
            return False
        if any(token.startswith(p) for p in prefixes if p.endswith("-")):
            return False
        if any(token.endswith(s) for s in suffixes if s.startswith("-")):
            return False
    return True


def main():
    vue_path = sys.argv[1]
    apply = "--apply" in sys.argv
    raw = io.open(vue_path, encoding="utf-8", newline="").read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    match = re.search(r"<style[^>]*>(.*)</style>", raw, re.S)
    if not match:
        print("no <style> block")
        return
    css = match.group(1)
    text = corpus()
    affixes = dynamic_affixes(text)
    out, removed, removed_chars = [], 0, 0
    for kind, prelude, body, raw_block in split_blocks(css):
        if kind == "atblock":
            inner = []
            for k2, p2, b2, r2 in split_blocks(body):
                if k2 == "rule" and is_dead(p2, text, affixes):
                    removed += 1
                    removed_chars += len(r2)
                    continue
                inner.append(f"{p2}{{{b2}}}" if k2 in ("rule", "atblock") else r2)
            if not "".join(inner).strip():
                removed_chars += len(body)
                continue
            out.append(f"{prelude}{{{''.join(inner)}}}")
        elif kind == "rule":
            if is_dead(prelude, text, affixes):
                removed += 1
                removed_chars += len(raw_block)
                continue
            out.append(raw_block)
        else:
            out.append(raw_block)
    result = re.sub(r"\n{3,}", "\n\n", "".join(out))
    print(f"{os.path.basename(vue_path)}: removed {removed} rules / {removed_chars} chars, "
          f"{len(css)} -> {len(result)}")
    if apply:
        io.open(vue_path, "w", encoding="utf-8", newline="").write(
            raw[: match.start(1)] + result + raw[match.end(1) :]
        )
        print("written")


if __name__ == "__main__":
    main()
