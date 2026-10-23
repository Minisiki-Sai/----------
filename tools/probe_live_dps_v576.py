from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.live_dps.process_reader import ProcessReadError, ReadOnlyProcess
from dragon_nest_builder.live_dps.profile import ClientProfile


KEYWORDS = (
    "damage",
    "dps",
    "mydamage",
    "partydamage",
    "damagedlg",
    "critical",
    "combat",
    "skilldamage",
    "heal",
    "party",
    "scdamage",
    "onrecv",
)


def _read_module(process: ReadOnlyProcess, base: int, size: int) -> tuple[bytes, list[dict[str, int]]]:
    chunk_size = 256 * 1024
    data = bytearray(size)
    failures: list[dict[str, int]] = []
    for offset in range(0, size, chunk_size):
        requested = min(chunk_size, size - offset)
        try:
            chunk = process.read(base + offset, requested)
            data[offset : offset + len(chunk)] = chunk
        except ProcessReadError:
            failures.append({"rva": offset, "size": requested})
    return bytes(data), failures


def _find_strings(raw: bytes) -> list[dict[str, object]]:
    hits: list[dict[str, object]] = []
    seen: set[tuple[int, str]] = set()
    for encoding, pattern in (
        ("ascii", re.compile(rb"[ -~]{5,}")),
        ("utf-16le", re.compile(rb"(?:[ -~]\x00){5,}")),
    ):
        for match in pattern.finditer(raw):
            text = match.group().decode(encoding, "ignore")
            lowered = text.casefold()
            matched = [keyword for keyword in KEYWORDS if keyword in lowered]
            if not matched:
                continue
            key = (match.start(), text)
            if key in seen:
                continue
            seen.add(key)
            hits.append(
                {
                    "rva": match.start(),
                    "encoding": encoding,
                    "keywords": matched,
                    "text": text[:300],
                }
            )
    hits.sort(key=lambda row: int(row["rva"]))
    return hits


def _pe_image_size(path: Path) -> int:
    with path.open("rb") as handle:
        dos = handle.read(0x40)
        if len(dos) < 0x40 or dos[:2] != b"MZ":
            raise SystemExit("客户端不是有效的 PE 文件")
        pe_offset = struct.unpack_from("<I", dos, 0x3C)[0]
        handle.seek(pe_offset)
        header = handle.read(4 + 20 + 64)
    if header[:4] != b"PE\0\0":
        raise SystemExit("客户端 PE 头无效")
    return struct.unpack_from("<I", header, 4 + 20 + 56)[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only v576 combat-data calibration probe")
    parser.add_argument(
        "--profile",
        type=Path,
        default=Path("dragon_nest_builder/data/live_dps_profiles/v576.json"),
    )
    parser.add_argument("--output", type=Path, default=Path("outputs/live_dps_v576_probe.json"))
    args = parser.parse_args()

    profile = ClientProfile.load(args.profile)
    valid, message = profile.validate_executable()
    if not valid:
        raise SystemExit(message)
    try:
        with ReadOnlyProcess.attach(profile.process_name) as process:
            try:
                module = process.module(profile.process_name)
            except ProcessReadError:
                base = process.image_base()
                from dragon_nest_builder.live_dps.process_reader import MemoryModule

                module = MemoryModule(
                    name=profile.process_name,
                    path=str(profile.executable_path),
                    base_address=base,
                    size=_pe_image_size(profile.executable_path),
                )
            raw, failures = _read_module(process, module.base_address, module.size)
    except ProcessReadError as exc:
        raise SystemExit(str(exc)) from None
    result = {
        "schema": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "profile_id": profile.profile_id,
        "pid": process.pid,
        "module": {
            "name": module.name,
            "path": module.path,
            "base_address": f"0x{module.base_address:X}",
            "size": module.size,
        },
        "read_failures": failures,
        "string_hits": _find_strings(raw),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{message}；模块 {module.size / 1024 / 1024:.1f} MB；命中 {len(result['string_hits'])} 条")
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
