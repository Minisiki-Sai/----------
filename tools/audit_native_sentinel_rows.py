from __future__ import annotations

import argparse
import json
import sys
from bisect import bisect_left
from collections import Counter, defaultdict
from pathlib import Path


def _text(value: object) -> str:
    return str(value or "").strip()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit native 0xFFFFFFFF damage rows against nearby actions."
    )
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--window-ms", type=int, default=5_000)
    args = parser.parse_args()

    project = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project))

    from dragon_nest_builder.data_loader import DATA_DIR
    from dragon_nest_builder.live_dps.models import CombatEvent, SkillCastEvent
    from dragon_nest_builder.live_dps.profile import ClientProfile
    from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource

    try:
        known_players = ClientProfile.load(
            DATA_DIR / "live_dps_profiles" / "v576.json"
        ).known_players
    except (OSError, ValueError, KeyError):
        known_players = {}

    source = V576DiagnosticReplaySource(args.capture.resolve(), DATA_DIR, known_players)
    try:
        events = source.poll()
    finally:
        source.close()

    casts_by_source: dict[str, list[SkillCastEvent]] = defaultdict(list)
    sentinels: list[CombatEvent] = []
    for event in events:
        if isinstance(event, SkillCastEvent):
            casts_by_source[event.source_id].append(event)
        elif (
            isinstance(event, CombatEvent)
            and not event.healing
            and event.amount > 0
            and (
                _text(event.raw_skill_id).casefold()
                in {"4294967295", "0xffffffff"}
                or "native_attack_result_sentinel"
                in _text(event.raw_attribution_method).casefold()
            )
        ):
            sentinels.append(event)
    for rows in casts_by_source.values():
        rows.sort(key=lambda row: row.timestamp_ms)

    counts: Counter[tuple[str, str, str]] = Counter()
    damage: Counter[tuple[str, str, str]] = Counter()
    samples: dict[tuple[str, str, str], list[dict[str, object]]] = defaultdict(list)
    for event in sentinels:
        casts = casts_by_source.get(event.source_id, [])
        timestamps = [row.timestamp_ms for row in casts]
        cursor = bisect_left(timestamps, event.timestamp_ms)
        nearby = [
            row
            for row in casts[max(0, cursor - 8) : cursor + 5]
            if abs(row.timestamp_ms - event.timestamp_ms) <= args.window_ms
        ]
        previous = [row for row in nearby if row.timestamp_ms <= event.timestamp_ms]
        next_rows = [row for row in nearby if row.timestamp_ms > event.timestamp_ms]
        previous_row = previous[-1] if previous else None
        next_row = next_rows[0] if next_rows else None
        key = (
            event.job or "<unknown>",
            _text(previous_row.skill_name) if previous_row else "<no-prev-action>",
            _text(previous_row.packet_kind) if previous_row else "",
        )
        counts[key] += 1
        damage[key] += event.amount
        if len(samples[key]) < 8:
            samples[key].append(
                {
                    "timeMs": event.timestamp_ms,
                    "amount": event.amount,
                    "decodedSkill": event.skill_name,
                    "decodedMethod": event.attribution_method,
                    "previous": (
                        {
                            "deltaMs": event.timestamp_ms
                            - previous_row.timestamp_ms,
                            "skillId": previous_row.skill_id,
                            "skillName": previous_row.skill_name,
                            "kind": previous_row.packet_kind,
                        }
                        if previous_row
                        else None
                    ),
                    "next": (
                        {
                            "deltaMs": next_row.timestamp_ms
                            - event.timestamp_ms,
                            "skillId": next_row.skill_id,
                            "skillName": next_row.skill_name,
                            "kind": next_row.packet_kind,
                        }
                        if next_row
                        else None
                    ),
                }
            )

    groups = [
        {
            "job": key[0],
            "previousSkill": key[1],
            "previousKind": key[2],
            "hits": counts[key],
            "damage": damage[key],
            "samples": samples[key],
        }
        for key in sorted(counts, key=lambda item: (-damage[item], item))
    ]
    report = {
        "capture": str(args.capture.resolve()),
        "sentinelHits": len(sentinels),
        "sentinelDamage": sum(row.amount for row in sentinels),
        "groups": groups,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
