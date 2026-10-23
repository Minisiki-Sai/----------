"""Audit weak cast-window attribution against stronger object/status evidence."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource  # noqa: E402


WEAK_METHODS = frozenset({"continuation_window", "supplemental_continuation"})
OBJECT_METHODS = frozenset(
    {"damage_object_projectile_link", "skill_object_owner", "summon_owner"}
)


def audit_capture(path: Path) -> dict[str, Any]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original = decoder.feed_events
    rows: list[dict[str, Any]] = []

    def feed_events(packet: object) -> Iterable[object]:
        emitted = tuple(original(packet))
        for event in emitted:
            if not isinstance(event, CombatEvent) or event.healing:
                continue
            rows.append(
                {
                    "time": int(event.timestamp_ms),
                    "source": str(event.source_id),
                    "job": str(event.job),
                    "skill": str(event.skill_id),
                    "name": str(event.skill_name),
                    "method": str(event.attribution_method),
                    "object": int(event.wire_damage_object_id or 0),
                    "group": int(event.wire_result_group_id or 0),
                    "damage": int(event.amount),
                }
            )
        return emitted

    decoder.feed_events = feed_events
    try:
        source.poll()
    finally:
        source.close()

    weak = Counter(
        (row["job"], row["skill"], row["name"], row["method"])
        for row in rows
        if row["method"] in WEAK_METHODS
    )
    by_object: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["object"] > 0:
            by_object[(row["source"], row["object"])].append(row)

    collisions: Counter[tuple[str, str, str, str, str]] = Counter()
    for object_rows in by_object.values():
        anchors = {
            (row["skill"], row["name"])
            for row in object_rows
            if row["method"] in OBJECT_METHODS
        }
        if len(anchors) != 1:
            continue
        anchor_skill, anchor_name = next(iter(anchors))
        for row in object_rows:
            if row["method"] not in WEAK_METHODS or row["skill"] == anchor_skill:
                continue
            collisions[
                (row["job"], row["skill"], row["name"], anchor_skill, anchor_name)
            ] += 1

    return {
        "capture": str(path),
        "events": len(rows),
        "weak": [
            {
                "job": key[0], "skill": key[1], "name": key[2],
                "method": key[3], "hits": count,
            }
            for key, count in weak.most_common()
        ],
        "object_collisions": [
            {
                "job": key[0], "weak_skill": key[1], "weak_name": key[2],
                "anchor_skill": key[3], "anchor_name": key[4], "hits": count,
            }
            for key, count in collisions.most_common()
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    reports = [audit_capture(path) for path in args.captures]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {"schema": "continuation-collision-v119", "captures": reports},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(args.output)
    for report in reports:
        if report["weak"] or report["object_collisions"]:
            print(Path(report["capture"]).name, report["events"])
            print(" weak", json.dumps(report["weak"], ensure_ascii=False))
            print(" collisions", json.dumps(report["object_collisions"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
