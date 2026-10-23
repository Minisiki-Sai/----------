from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.models import SkillCastEvent
from dragon_nest_builder.live_dps.profile import ClientProfile
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource


def _load(path: Path, data_root: Path, known_players: dict, mode: str):
    started = time.perf_counter()
    source = V576DiagnosticReplaySource(
        path,
        data_root,
        known_players,
        action_parser_mode=mode,
    )
    try:
        events = source.poll()
    finally:
        source.close()
    aggregator = CombatAggregator()
    for event in events:
        aggregator.ingest(event)
    snapshot = aggregator.snapshot()
    casts = Counter(
        (event.timestamp_ms, event.source_id, event.skill_id, event.skill_name)
        for event in events
        if isinstance(event, SkillCastEvent)
    )
    return snapshot, casts, time.perf_counter() - started


def compare(path: Path, data_root: Path, known_players: dict) -> dict[str, object]:
    legacy, legacy_casts, legacy_seconds = _load(
        path, data_root, known_players, "legacy"
    )
    structured, structured_casts, structured_seconds = _load(
        path, data_root, known_players, "structured"
    )
    removed = legacy_casts - structured_casts
    added = structured_casts - legacy_casts
    legacy_player_damage = {row.source_id: row.damage for row in legacy.players}
    structured_player_damage = {
        row.source_id: row.damage for row in structured.players
    }
    checks = {
        "total_damage_preserved": legacy.total_damage == structured.total_damage,
        "total_healing_preserved": legacy.total_healing == structured.total_healing,
        "raw_ledger_preserved": legacy.raw_damage_total == structured.raw_damage_total,
        "attributed_ledger_preserved": (
            legacy.attributed_damage_total == structured.attributed_damage_total
        ),
        "player_damage_preserved": legacy_player_damage == structured_player_damage,
        "legacy_casts_preserved": not removed,
    }
    return {
        "capture": str(path),
        "passed": all(checks.values()),
        "checks": checks,
        "legacy_seconds": round(legacy_seconds, 3),
        "structured_seconds": round(structured_seconds, 3),
        "speed_ratio": round(structured_seconds / max(0.001, legacy_seconds), 3),
        "legacy_casts": sum(legacy_casts.values()),
        "structured_casts": sum(structured_casts.values()),
        "added_casts": [
            {
                "timestamp_ms": key[0],
                "source_id": key[1],
                "skill_id": key[2],
                "skill_name": key[3],
                "count": count,
            }
            for key, count in added.items()
        ],
        "removed_casts": [
            {
                "timestamp_ms": key[0],
                "source_id": key[1],
                "skill_id": key[2],
                "skill_name": key[3],
                "count": count,
            }
            for key, count in removed.items()
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare legacy and structured multi-event action parsers."
    )
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    data_root = ROOT / "dragon_nest_builder" / "data"
    profile = ClientProfile.load(data_root / "live_dps_profiles" / "v576.json")
    rows = [compare(path.resolve(), data_root, profile.known_players) for path in args.captures]
    payload = {"passed": all(bool(row["passed"]) for row in rows), "captures": rows}
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    print(text)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
