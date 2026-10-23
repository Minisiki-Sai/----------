from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


def text(value: object) -> str:
    return str(value or "").strip()


def number(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def split_jobs(value: object) -> tuple[str, ...]:
    return tuple(
        token.strip()
        for token in re.split(r"[、,，/]+", text(value))
        if token.strip()
    )


def speed_auto_applies(row: dict[str, Any]) -> bool:
    scope = text(row.get("scope"))
    if scope == "技能自身":
        return True
    duration = number(row.get("duration_seconds")) or 0.0
    return scope == "自身状态窗口" and 0.0 < duration <= 5.0


def generate(source: Path) -> dict[str, object]:
    raw_bytes = source.read_bytes()
    payload = json.loads(raw_bytes.decode("utf-8-sig"))
    fps = number(payload.get("fps")) or 60.0

    speed_by_id: dict[tuple[str, int], float] = {}
    for row in payload.get("action_speed_rows") or ():
        if not isinstance(row, dict) or not speed_auto_applies(row):
            continue
        try:
            skill_id = int(row.get("skill_id") or 0)
        except (TypeError, ValueError):
            continue
        speed = max(0.0, number(row.get("speed_percent")) or 0.0)
        for job in split_jobs(row.get("jobs")):
            key = (job, skill_id)
            speed_by_id[key] = max(speed_by_id.get(key, 0.0), speed)

    windows_by_skill: dict[
        tuple[str, int, str], set[tuple[float, float]]
    ] = defaultdict(set)
    forms_by_skill: dict[tuple[str, int, str], set[str]] = defaultdict(set)
    for row in payload.get("event_rows") or ():
        if not isinstance(row, dict):
            continue
        if text(row.get("event_type")).casefold() != "hit":
            continue
        weight = number(row.get("weight"))
        if weight is None or abs(weight) <= 1e-12:
            continue
        if text(row.get("effective_skill")) == "否":
            continue
        job = text(row.get("job"))
        skill_name = text(row.get("skill") or row.get("skill_name"))
        try:
            skill_id = int(row.get("skill_id") or 0)
        except (TypeError, ValueError):
            continue
        if not job or not skill_id or not skill_name:
            continue
        start = number(row.get("seconds"))
        end = number(row.get("seconds_end"))
        if start is None:
            frame = number(row.get("frame"))
            start = frame / fps if frame is not None else None
        if start is None:
            continue
        if end is None:
            frame_end = number(row.get("frame_end"))
            end = frame_end / fps if frame_end is not None else start
        speed = speed_by_id.get((job, skill_id), 0.0)
        factor = 1.0 / (1.0 + speed / 100.0)
        low, high = sorted(
            (
                max(0.0, start * 1000.0 * factor),
                max(0.0, (end if end is not None else start) * 1000.0 * factor),
            )
        )
        key = (job, skill_id, skill_name)
        windows_by_skill[key].add((round(low, 3), round(high, 3)))
        forms_by_skill[key].add(text(row.get("form")) or "当前主结果")

    profiles = []
    for key in sorted(windows_by_skill, key=lambda item: (item[0], item[1], item[2])):
        job, skill_id, skill_name = key
        profiles.append(
            {
                "job": job,
                "skill_id": str(skill_id),
                "skill_name": skill_name,
                "windows_ms": [list(row) for row in sorted(windows_by_skill[key])],
                "forms": sorted(forms_by_skill[key]),
            }
        )
    return {
        "schema_version": 1,
        "schema": "action-hit-timing-v1",
        "source_schema": text(payload.get("schema")),
        "source_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "fps": fps,
        "profiles": profiles,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate the compact ACT action-to-Hit timing catalog."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    payload = generate(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "profiles": len(payload["profiles"]),
                "bytes": args.output.stat().st_size,
                "source_sha256": payload["source_sha256"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
