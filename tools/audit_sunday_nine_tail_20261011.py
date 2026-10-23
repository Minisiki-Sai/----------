"""Read-only cloud audit of the requested character's black-dragon timelines."""
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps.supabase_score_sync import TeamScoreSupabaseSyncService
from dragon_nest_builder.live_dps.v410_score_sync import load_v410_score_sync_config


def decode(timeline, section):
    start = 0
    quantum = timeline.get("q", 100)
    result = []
    for row in timeline.get(section, ()):
        start += row[0]
        result.append({"start": start / quantum, "end": (start + row[1]) / quantum,
                       "skill": timeline["n"][row[2]] if len(row) > 2 else "output",
                       "extra": row[3:]})
    return result


if __name__ == "__main__":
    config = load_v410_score_sync_config()
    service = TeamScoreSupabaseSyncService(config.endpoint, config.upload_token,
                                         config.installation_id, timeout_seconds=45)
    page = service.fetch_score_history_page(jobs=("时空领主",),
        character_search="还好不是星期天", limit=50)
    items = [r for r in page["items"] if r["nest"] in ("黑龙经典", "黑龙硬核")]
    records = []
    for item in items[:10]:
        detail = service.fetch_profession_history_detail("时空领主", item["historyId"])
        if not detail:
            continue
        timeline = detail.get("compactTimeline", {})
        actual, theory = decode(timeline, "a"), decode(timeline, "t")
        entry = {"recordedAt": item["recordedAt"], "nest": item["nest"],
                 "historyId": item["historyId"], "schema": item["score_schema_version"],
                 "skills": detail.get("skillDetails", []), "detail": detail,
                 "actualDecoded": actual, "theoryDecoded": theory,
                 "effectsDecoded": decode(timeline, "e")}
        records.append(entry)
        print(json.dumps({k: entry[k] for k in ("recordedAt", "nest", "schema")}
                         | {"nineTail": [r for r in entry["skills"] if "九尾" in r.get("skill_name", "")],
                            "actual": dict(Counter(r["skill"] for r in actual)),
                            "theory": dict(Counter(r["skill"] for r in theory)),
                            "timelineTruncated": timeline.get("x", [])}, ensure_ascii=False), flush=True)
    out = ROOT / "work/sunday_nine_tail_cloud_20261011.json"
    out.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Saved audit:", out, flush=True)
