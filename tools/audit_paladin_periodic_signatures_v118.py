"""Measure packet-shape and cadence separation for Paladin periodic effects."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import statistics
import sys
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource  # noqa: E402


JOBS = frozenset({"圣骑士", "十字军"})


def audit(path: Path) -> dict[str, Any]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original = decoder.feed_events
    rows: list[dict[str, Any]] = []

    def feed_events(packet: object) -> Iterable[object]:
        emitted = tuple(original(packet))
        signature = bytes(getattr(packet, "payload", b""))[5:9].hex()
        payload_length = len(bytes(getattr(packet, "payload", b"")))
        for event in emitted:
            if not isinstance(event, CombatEvent) or event.healing or event.job not in JOBS:
                continue
            rows.append(
                {
                    "time": int(event.timestamp_ms),
                    "source": str(event.source_id),
                    "target": str(event.target_id),
                    "skill": str(event.skill_id),
                    "name": str(event.skill_name),
                    "method": str(event.attribution_method),
                    "layout": str(event.wire_damage_layout),
                    "signature": signature,
                    "payload_length": payload_length,
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

    shapes = Counter(
        (row["method"], row["skill"], row["payload_length"], row["signature"], row["layout"])
        for row in rows
    )
    grouped: dict[tuple[str, str, str, str], list[int]] = defaultdict(list)
    for row in rows:
        grouped[(row["source"], row["target"], row["method"], row["skill"])].append(row["time"])
    cadence: dict[tuple[str, str], list[float]] = defaultdict(list)
    for (_source, _target, method, skill), times in grouped.items():
        times.sort()
        cadence[(method, skill)].extend(
            (right - left) / 1000.0 for left, right in zip(times, times[1:])
            if 0 < right - left <= 5000
        )
    cadence_rows = []
    for key, values in sorted(cadence.items()):
        values.sort()
        if not values:
            continue
        cadence_rows.append(
            {
                "method": key[0],
                "skill": key[1],
                "intervals": len(values),
                "median": round(statistics.median(values), 4),
                "p10": values[int((len(values) - 1) * 0.10)],
                "p90": values[int((len(values) - 1) * 0.90)],
            }
        )
    object_groups: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if int(row["object"]) > 0:
            object_groups[(row["source"], int(row["object"]))].append(row)
    object_links: Counter[tuple[str, str, str, str]] = Counter()
    for group_rows in object_groups.values():
        anchors = {
            (row["skill"], row["name"])
            for row in group_rows
            if row["method"] == "damage_object_projectile_link"
        }
        if len(anchors) != 1:
            continue
        anchor_skill, anchor_name = next(iter(anchors))
        for row in group_rows:
            if row["method"] == "damage_object_projectile_link":
                continue
            object_links[(anchor_skill, anchor_name, row["method"], row["skill"])] += 1
    return {
        "capture": str(path),
        "rows": len(rows),
        "shapes": [
            {
                "method": key[0], "skill": key[1], "payload_length": key[2],
                "signature": key[3], "layout": key[4], "hits": count,
            }
            for key, count in shapes.most_common()
        ],
        "cadence": cadence_rows,
        "same_object_links": [
            {
                "anchor_skill": key[0], "anchor_name": key[1],
                "weak_method": key[2], "weak_skill": key[3], "hits": count,
            }
            for key, count in object_links.most_common()
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    reports = [audit(path) for path in args.captures]
    result = {"schema": "paladin-periodic-signatures-v118", "captures": reports}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)
    for report in reports:
        print(Path(report["capture"]).name, report["rows"])
        for row in report["shapes"][:30]:
            if row["method"] in {"refreshable_pulsing_aura", "periodic_status", "continuation_window", "unresolved", "latest_profiled_direct_cast"}:
                print(json.dumps(row, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
