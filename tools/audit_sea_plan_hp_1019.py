"""Offline HP/action/cast evidence for a raid plan; never uploads or scores."""
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps.capture_sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.models import (
    BossHealthEvent, BossActionEvent, BossSpawnEvent, PlayerLifecycleEvent,
    MonsterAppearanceEvent, SkillCastEvent,
)

OUT = ROOT / "outputs/verification/sea_support_timeline_1019/hp_actions"
CAP = Path("C:/Users/gcg59/Documents/DragonNestDpsCapture/live_dps_diagnostics")

def run(number):
    path = next(CAP.glob(f"1019噬魂*第{number}把.dpscap"))
    dest = OUT / f"{path.stem}.json"
    if dest.exists():
        print("EXISTS", dest.name, flush=True)
        return
    started = time.perf_counter()
    source = V576DiagnosticReplaySource(path, ROOT / "dragon_nest_builder/data")
    try:
        events = source.poll()
        classes = (BossHealthEvent, BossActionEvent, BossSpawnEvent,
                   PlayerLifecycleEvent, MonsterAppearanceEvent, SkillCastEvent)
        data = {c.__name__: [asdict(e) for e in events if isinstance(e, c)] for c in classes}
        data["file"] = path.name
        data["elapsed"] = time.perf_counter() - started
        dest.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        print(path.name, round(data["elapsed"], 2),
              {c.__name__: len(data[c.__name__]) for c in classes}, flush=True)
    finally:
        source.close()

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for number in sys.argv[1:] or ["十七", "五", "十四", "十", "十九"]:
        run(number)
