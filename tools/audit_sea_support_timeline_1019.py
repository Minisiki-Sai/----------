"""Offline evidence for a player-facing sea-dragon support timeline; no scoring/upload."""
import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps.capture_sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.models import CombatEvent, SkillCastEvent, BossStatusEvent, BossActionEvent, SkillParameterEvent

OUT = ROOT / "outputs/verification/sea_support_timeline_1019/three_buffs"
CAP = Path("C:/Users/gcg59/Documents/DragonNestDpsCapture/live_dps_diagnostics")
NAMES = {"灼热火焰", "精神爆发", "时间加速", "幽灵之魂"}
IDS = {"2208", "1412", "2415", "5007"}


def run(path):
    dest = OUT / (path.stem + ".json")
    if dest.exists():
        print("EXISTS", path.name, flush=True)
        return
    start = time.perf_counter()
    source = V576DiagnosticReplaySource(path, ROOT / "dragon_nest_builder/data")
    try:
        events = source.poll()
        players = {}
        casts, statuses, parameters, actions = [], [], [], []
        targets = {}
        totals = defaultdict(int)
        cast_names = Counter()
        for e in events:
            name = getattr(e, "skill_name", "")
            wanted = name in NAMES or str(getattr(e, "skill_id", "")) in IDS
            if isinstance(e, SkillCastEvent):
                cast_names[name] += 1
                if wanted:
                    casts.append(asdict(e))
            if isinstance(e, BossStatusEvent) and wanted:
                statuses.append(asdict(e))
            if isinstance(e, SkillParameterEvent) and wanted:
                parameters.append(asdict(e))
            if isinstance(e, BossActionEvent):
                actions.append(asdict(e))
            if isinstance(e, CombatEvent) and not e.healing and e.amount > 0:
                player = players.setdefault(e.source_id, dict(name=e.source_name, job=e.job, damage=0))
                player["damage"] += e.amount
                if e.source_name:
                    player["name"] = e.source_name
                if e.job:
                    player["job"] = e.job
                t = targets.setdefault(e.target_id, dict(name=e.target_name, first_ms=e.timestamp_ms,
                     last_ms=e.timestamp_ms, damage=0, max_hp=0, seconds=defaultdict(int), players=defaultdict(int),
                     player_seconds=defaultdict(lambda: defaultdict(int))))
                t["first_ms"] = min(t["first_ms"], e.timestamp_ms)
                t["last_ms"] = max(t["last_ms"], e.timestamp_ms)
                t["damage"] += e.amount
                t["max_hp"] = max(t["max_hp"], e.target_max_hp)
                t["seconds"][e.timestamp_ms // 1000] += e.amount
                t["players"][e.source_id] += e.amount
                t["player_seconds"][e.source_id][e.timestamp_ms // 1000] += e.amount
                if e.target_name:
                    t["name"] = e.target_name
                totals[e.timestamp_ms // 1000] += e.amount
        result = dict(file=path.name, elapsed=time.perf_counter()-start,
            event_types=dict(Counter(type(e).__name__ for e in events)),
            capture_integrity=source.capture_integrity,
            map_history=getattr(source._decoder, "detected_map_history", []),
            players=players, targets=targets, casts=casts, statuses=statuses,
            parameters=parameters, boss_actions=actions, seconds=totals,
            cast_names=cast_names)
        dest.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(dict(file=path.name, seconds=round(result["elapsed"], 2),
            casts=len(casts), statuses=len(statuses), targets=len(targets)), ensure_ascii=False), flush=True)
    finally:
        source.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--capture", type=Path)
    args = p.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    paths = [args.capture] if args.capture else sorted(CAP.glob("1019噬魂*.dpscap"),
        key=lambda p: (not ("第十七把" in p.name), p.name))
    for path in paths:
        run(path)
