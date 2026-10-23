from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the installer integrity manifest")
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()

    root = args.project_root.resolve()
    runtime = root / "runtime" / "DragonNestEngine"
    npcap = root / "third_party" / "npcap" / "npcap-1.88.exe"
    verifier = root / "tools" / "verify_install_manifest.ps1"
    required = (runtime, npcap, verifier)
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit("Missing install payload:\n" + "\n".join(missing))

    sources: list[tuple[Path, str]] = []
    for path in sorted(item for item in runtime.rglob("*") if item.is_file()):
        relative = path.relative_to(runtime).as_posix()
        sources.append((path, f"runtime/DragonNestEngine/{relative}"))
    sources.append((npcap, "third_party/npcap/npcap-1.88.exe"))

    # Never bundle the live executable at its old fixed destination. The
    # installer expands this archive into a fresh release directory instead.
    archive = root / "runtime" / "engine-package.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        for path in sorted(item for item in runtime.rglob("*") if item.is_file()):
            package.write(path, path.relative_to(runtime).as_posix())

    files = [
        {
            "path": destination,
            "size": source.stat().st_size,
            "sha256": sha256(source),
        }
        for source, destination in sources
    ]
    manifest = {
        "schemaVersion": 1,
        "appVersion": args.version,
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "algorithm": "SHA-256",
        "engineArchive": {
            "path": "runtime/engine-package.zip",
            "size": archive.stat().st_size,
            "sha256": sha256(archive),
        },
        "files": files,
    }
    output = root / "runtime" / "install-manifest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"Wrote {output} with {len(files)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
