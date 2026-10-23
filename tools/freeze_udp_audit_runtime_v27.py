"""Freeze the complete Python package and data, not a partial mixin overlay."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil


def freeze(root: Path, destination: Path) -> dict:
    root, destination = root.resolve(), destination.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    files = sorted(p for p in (root / "dragon_nest_builder").rglob("*")
                   if p.is_file() and "__pycache__" not in p.parts
                   and (p.suffix == ".py" or "data" in p.relative_to(root).parts))
    files += [root / "tools" / name for name in
              ("audit_udp_damage_integrity_v27.py", "freeze_udp_audit_runtime_v27.py")]
    hashes = {str(p.relative_to(root)).replace("\\", "/"):
              hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    destination.mkdir(parents=True)
    for p in files:
        target = destination / p.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, target)
    for rel, expected in hashes.items():
        if (hashlib.sha256((root / rel).read_bytes()).hexdigest() != expected
                or hashlib.sha256((destination / rel).read_bytes()).hexdigest() != expected):
            raise RuntimeError(f"Concurrent input change: {rel}; snapshot is incomplete")
    manifest = {"schema": "full-udp-audit-runtime-v27", "root": str(root), "files": hashes}
    with (destination / "manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2, ensure_ascii=False)
    return {"snapshot": str(destination), "files": len(hashes)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(json.dumps(freeze(Path(__file__).resolve().parents[1], args.destination), ensure_ascii=False))
