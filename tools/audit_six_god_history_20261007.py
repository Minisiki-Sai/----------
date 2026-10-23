"""Read only the two requested public score details; never writes cloud data."""
from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dragon_nest_builder.live_dps.supabase_score_sync import TeamScoreSupabaseSyncService
from dragon_nest_builder.live_dps.v410_score_sync import load_v410_score_sync_config


def decode(timeline, section):
    start = 0
    quantum = timeline.get("q", 100)
    result = []
    for row in timeline.get(section, ()):
        start += row[0]
        result.append((start / quantum, (start + row[1]) / quantum,
                       timeline["n"][row[2]] if len(row) > 2 else "output",
                       row[3] if len(row) > 3 else 0))
    return result


def union_seconds(intervals):
    total = 0.0
    end = float("-inf")
    for start, stop in sorted(intervals):
        if stop > end:
            total += max(0, stop - max(start, end))
            end = stop
    return round(total, 4)


def summarize(timeline, section, output):
    grouped = defaultdict(list)
    for start, end, name, lane in decode(timeline, section):
        grouped[(name, lane)].append((start, end))
    result = []
    for (name, lane), intervals in sorted(grouped.items()):
        overlap = [(max(start, left), min(end, right))
                   for start, end in intervals for left, right, *_ in output
                   if max(start, left) < min(end, right)]
        result.append({"name": name, "lane": lane, "count": len(intervals),
                       "seconds": union_seconds(intervals),
                       "output_overlap_seconds": union_seconds(overlap),
                       "first": intervals[0][0], "last": intervals[-1][1],
                       "intervals": intervals if section == "e" and
                       any(key in name for key in ("化身", "判定", "光环", "审判力量")) else []})
    return result


def main():
    config = load_v410_score_sync_config()
    service = TeamScoreSupabaseSyncService(
        config.endpoint, config.upload_token, config.installation_id, timeout_seconds=45)
    ids = (
        "b7c1227d4dbc719c87284e9ad3cf66cdcda8bd20adc484ebfeb4f12f926955e5:3",
        "e7b54cf28fa91ae1c0e0a628b5c86f1315d1a1a3908388716a21cca730dbc2af:7",
    )
    records = []
    for identifier in ids:
        detail = service.fetch_profession_history_detail("十字军", identifier)
        timeline = detail["compactTimeline"]
        output = decode(timeline, "o")
        records.append({"history_id": identifier, "recorded_at": detail["recordedAt"],
                        "duration": timeline["d"] / timeline["q"],
                        "output_seconds": union_seconds([(a, b) for a, b, *_ in output]),
                        "output_windows": output, "truncated": timeline.get("x", []),
                        "actual": summarize(timeline, "a", output),
                        "theory": summarize(timeline, "t", output),
                        "effects": summarize(timeline, "e", output)})
    print(json.dumps(records, ensure_ascii=False))


if __name__ == "__main__":
    main()
