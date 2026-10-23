from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v410_backend import Engine


def main() -> None:
    engine = Engine()
    core = engine.native
    members = [
        "噬魂者",
        "黑暗女王",
        "时空领主",
        "风行者",
        "影舞者",
        "圣徒",
        "圣骑士",
        "毁灭者",
    ]
    payload = {
        "members": members,
        "window": 180,
        "boss": "红龙佩达",
        "freezeCoefficient": 0.3,
        "auraChoices": {"圣骑士": "判定光环", "十字军": "元素光环"},
        "cdrUsage": {"时间加速": 1, "精神爆发": 1, "鸡尾酒": 1},
        "cdrGlyphs": {"时间加速": True, "精神爆发": True},
        "cdrApplies": {"时间加速": False, "精神爆发": False, "鸡尾酒": False},
        "mechanisms": [
            {"name": "测试机制1", "start": 35.0, "end": 52.0},
            {"name": "测试机制2", "start": 104.0, "end": 126.0},
        ],
    }
    events: list[tuple[int, str]] = []
    started = time.perf_counter()
    result = core.custom_team_preview(
        payload,
        lambda progress, label: events.append((progress, label)),
    )
    assert len(result["members"]) == 8
    assert result["planner"] == "V4.01自选机制轴完整同源管线"
    assert result["blockedSeconds"] == 39.0
    assert abs(sum(row["share"] for row in result["members"]) - 1.0) < 1e-9
    assert all(row["timeline"]["legacyParity"] for row in result["members"])
    assert all(row["timeline"]["segments"] for row in result["members"])
    assert events and events[-1][0] == 100
    print(json.dumps({
        "elapsed": round(time.perf_counter() - started, 3),
        "dps": round(result["dps"], 3),
        "activeDps": round(result["activeDps"], 3),
        "scheduleMode": result["scheduleMode"],
        "buffCasts": len(result["buffSchedule"]),
        "segmentCounts": {
            row["job"]: len(row["timeline"]["segments"])
            for row in result["members"]
        },
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
