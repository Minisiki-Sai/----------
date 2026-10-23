from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def _normalise(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return _normalise(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {
            str(key): _normalise(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalise(item) for item in value]
    if isinstance(value, (set, frozenset)):
        rows = [_normalise(item) for item in value]
        return sorted(rows, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
    if isinstance(value, float):
        if not math.isfinite(value):
            return str(value)
        return round(value, 9)
    if isinstance(value, Path):
        return str(value)
    return value


def _digest(value: Any) -> str:
    payload = json.dumps(
        _normalise(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--capture", type=Path, required=True)
    args = parser.parse_args()

    project = args.project.resolve()
    capture = args.capture.resolve()
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.path.insert(0, str(project))

    from dragon_nest_builder.data_loader import DATA_DIR
    from dragon_nest_builder.live_dps.aggregator import CombatAggregator
    from dragon_nest_builder.live_dps.profile import ClientProfile
    from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource

    profile_path = DATA_DIR / "live_dps_profiles" / "v576.json"
    try:
        known_players = ClientProfile.load(profile_path).known_players
    except (OSError, ValueError, KeyError):
        known_players = {}

    source = V576DiagnosticReplaySource(capture, DATA_DIR, known_players)
    try:
        events = source.poll()
    finally:
        source.close()

    aggregator = CombatAggregator()
    for event in events:
        aggregator.ingest(event)
    snapshot = aggregator.snapshot(include_events=True)

    fields: dict[str, dict[str, Any]] = {}
    for field in dataclasses.fields(snapshot):
        value = getattr(snapshot, field.name)
        fields[field.name] = {
            "count": len(value) if isinstance(value, (dict, list, tuple, set, frozenset)) else None,
            "sha256": _digest(value),
        }

    report = {
        "project": str(project),
        "captureSha256": hashlib.sha256(capture.read_bytes()).hexdigest(),
        "decodedEventCount": len(events),
        "decodedEventTypes": dict(sorted(Counter(type(event).__name__ for event in events).items())),
        "decodedEventsSha256": _digest(events),
        "snapshotSha256": _digest(snapshot),
        "totals": {
            "elapsedSeconds": round(float(snapshot.elapsed_seconds), 9),
            "totalDamage": int(snapshot.total_damage),
            "totalHealing": int(snapshot.total_healing),
            "rawDamage": int(snapshot.raw_damage_total),
            "attributedDamage": int(snapshot.attributed_damage_total),
            "unresolvedDamage": int(snapshot.unresolved_damage_total),
            "inferredCasts": int(snapshot.inferred_cast_count),
            "reassignedHits": int(snapshot.reassigned_hit_count),
        },
        "fields": fields,
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
