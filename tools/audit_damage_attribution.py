from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def _event_fingerprint(event: Any) -> str:
    payload = "|".join(
        (
            str(event.timestamp_ms),
            event.source_id,
            event.target_id,
            str(event.amount),
            "1" if event.critical else "0",
            "1" if event.healing else "0",
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay one dpscap and audit damage-attribution evidence."
    )
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    project = Path(__file__).resolve().parents[1]
    capture = args.capture.resolve()
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.path.insert(0, str(project))

    from dragon_nest_builder.data_loader import DATA_DIR
    from dragon_nest_builder.live_dps.aggregator import CombatAggregator
    from dragon_nest_builder.live_dps.damage_attribution import (
        attribution_layer,
        is_deterministic_attribution,
    )
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

    methods: Counter[str] = Counter()
    method_damage: Counter[str] = Counter()
    layers: Counter[str] = Counter()
    layer_damage: Counter[str] = Counter()
    deterministic_hits = 0
    deterministic_damage = 0
    factual_fingerprint = hashlib.sha256()
    player_skills: dict[str, Counter[tuple[str, str]]] = defaultdict(Counter)
    player_skill_methods: dict[
        tuple[str, str, str], Counter[str]
    ] = defaultdict(Counter)
    player_skill_method_damage: dict[
        tuple[str, str, str], Counter[str]
    ] = defaultdict(Counter)
    player_meta: dict[str, tuple[str, str]] = {}
    for event in snapshot.combat_events:
        if event.healing:
            continue
        method = str(event.attribution_method or "unresolved").strip().casefold()
        layer = attribution_layer(method).value
        methods[method] += 1
        method_damage[method] += int(event.amount)
        layers[layer] += 1
        layer_damage[layer] += int(event.amount)
        if is_deterministic_attribution(method):
            deterministic_hits += 1
            deterministic_damage += int(event.amount)
        factual_fingerprint.update(_event_fingerprint(event).encode("ascii"))
        player_meta[event.source_id] = (event.source_name, event.job)
        player_skills[event.source_id][
            (event.skill_id, event.skill_name)
        ] += int(event.amount)
        skill_method_key = (
            event.source_id,
            event.skill_id,
            event.skill_name,
        )
        player_skill_methods[skill_method_key][method] += 1
        player_skill_method_damage[skill_method_key][method] += int(
            event.amount
        )

    players: list[dict[str, Any]] = []
    for source_id, skills in player_skills.items():
        name, job = player_meta[source_id]
        total = sum(skills.values())
        players.append(
            {
                "sourceId": source_id,
                "name": name,
                "job": job,
                "damage": total,
                "skills": [
                    {
                        "skillId": skill_id,
                        "skillName": skill_name,
                        "damage": damage,
                        "share": round(damage / total * 100.0, 6) if total else 0.0,
                        "attribution": {
                            method: {
                                "hits": player_skill_methods[
                                    (source_id, skill_id, skill_name)
                                ][method],
                                "damage": player_skill_method_damage[
                                    (source_id, skill_id, skill_name)
                                ][method],
                            }
                            for method in sorted(
                                player_skill_methods[
                                    (source_id, skill_id, skill_name)
                                ]
                            )
                        },
                    }
                    for (skill_id, skill_name), damage in sorted(
                        skills.items(), key=lambda row: (-row[1], row[0][1])
                    )
                ],
            }
        )
    players.sort(key=lambda row: (-row["damage"], row["sourceId"]))

    report = {
        "capture": str(capture),
        "captureSha256": hashlib.sha256(capture.read_bytes()).hexdigest(),
        "factualDamageFingerprint": factual_fingerprint.hexdigest(),
        "decodedEventCount": len(events),
        "totals": {
            "damage": int(snapshot.total_damage),
            "rawDamage": int(snapshot.raw_damage_total),
            "attributedDamage": int(snapshot.attributed_damage_total),
            "unresolvedDamage": int(snapshot.unresolved_damage_total),
            "combatHits": len(snapshot.combat_events),
            "deterministicHits": deterministic_hits,
            "deterministicDamage": deterministic_damage,
            "inferredCasts": int(snapshot.inferred_cast_count),
            "reassignedHits": int(snapshot.reassigned_hit_count),
        },
        "layers": {
            key: {"hits": layers[key], "damage": layer_damage[key]}
            for key in sorted(layers)
        },
        "methods": {
            key: {"hits": methods[key], "damage": method_damage[key]}
            for key in sorted(methods)
        },
        "players": players,
        "visiblePlayers": [
            {
                "sourceId": player.source_id,
                "name": player.name,
                "job": player.job,
                "damage": int(player.damage),
                "skills": [
                    {
                        "skillId": skill.skill_id,
                        "skillName": skill.name,
                        "damage": int(skill.damage),
                        "hits": int(skill.hits),
                        "share": round(float(skill.share) * 100.0, 6),
                        "attribution": dict(skill.attribution),
                    }
                    for skill in player.skills
                    if skill.damage > 0
                ],
            }
            for player in snapshot.players
        ],
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
