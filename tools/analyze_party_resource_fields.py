from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from dragon_nest_builder.live_dps.models import (
    CombatEvent,
    PartyMemberEvent,
    PlayerLifecycleEvent,
)
from dragon_nest_builder.live_dps.sources import _V576PacketEventDecoder
from tools.analyze_party_hp_wire import _iter_packets, _phase_and_body


def analyze(path: Path, data_root: Path) -> None:
    packets = list(_iter_packets(path))
    decoder = _V576PacketEventDecoder(data_root)
    first = packets[0].timestamp_seconds if packets else 0.0
    raw: list[tuple[object, list[object]]] = []
    players: set[int] = set()
    deltas: dict[int, list[tuple[float, int, str]]] = defaultdict(list)
    lifecycle: dict[int, list[tuple[float, bool]]] = defaultdict(list)
    for packet in packets:
        events = decoder.feed_events(packet)
        raw.append((packet, events))
        for event in events:
            if isinstance(event, PartyMemberEvent):
                try:
                    players.add(int(event.actor_id, 16))
                except ValueError:
                    pass
            elif isinstance(event, PlayerLifecycleEvent):
                try:
                    actor_id = int(event.target_id, 16)
                except ValueError:
                    continue
                players.add(actor_id)
                lifecycle[actor_id].append(
                    (first + event.timestamp_ms / 1000.0, event.active)
                )
            elif isinstance(event, CombatEvent) and event.target_id:
                try:
                    actor_id = int(event.target_id, 16)
                except ValueError:
                    continue
                if event.attribution_method == "incoming_damage":
                    players.add(actor_id)
                    deltas[actor_id].append(
                        (first + event.timestamp_ms / 1000.0, -event.amount, "damage")
                    )
                elif event.healing and actor_id in players:
                    deltas[actor_id].append(
                        (first + event.timestamp_ms / 1000.0, event.amount, "healing")
                    )

    fields: dict[bytes, dict[int, list[tuple[float, int]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for packet, _events in raw:
        if packet.inferred_direction() != "server_to_client":
            continue
        _phase, body = _phase_and_body(packet.payload, players)
        for actor_id in players:
            actor = actor_id.to_bytes(4, "little")
            offset = body.find(actor, 9)
            while offset >= 0:
                if offset + 11 <= len(body):
                    code = body[offset + 4 : offset + 7]
                    value = int.from_bytes(body[offset + 7 : offset + 11], "little")
                    if value <= 5_000_000:
                        fields[code][actor_id].append((packet.timestamp_seconds, value))
                offset = body.find(actor, offset + 1)

    rankings: list[tuple[float, str]] = []
    for code, by_actor in fields.items():
        sample_count = sum(len(rows) for rows in by_actor.values())
        if sample_count < 20 or len(by_actor) < 2:
            continue
        damage_total = damage_match = healing_total = healing_match = 0
        changed = zero_count = 0
        minimum = 2**32 - 1
        maximum = 0
        near_death_zero = death_checks = 0
        for actor_id, rows in by_actor.items():
            states = sorted(set(rows))
            values = [value for _time, value in states]
            minimum = min(minimum, min(values, default=minimum))
            maximum = max(maximum, max(values, default=0))
            zero_count += sum(value == 0 for value in values)
            actor_deltas = sorted(deltas[actor_id])
            for previous, current in zip(states, states[1:]):
                wire_delta = current[1] - previous[1]
                if wire_delta:
                    changed += 1
                interval = [
                    row
                    for row in actor_deltas
                    if previous[0] < row[0] <= current[0] + 0.001
                ]
                kinds = {kind for _time, _value, kind in interval}
                if kinds == {"damage"}:
                    damage_total += 1
                    damage_match += wire_delta < 0
                elif kinds == {"healing"}:
                    healing_total += 1
                    healing_match += wire_delta > 0
            for timestamp, active in lifecycle[actor_id]:
                if active:
                    continue
                following = next(
                    (value for time, value in states if timestamp <= time <= timestamp + 0.5),
                    None,
                )
                if following is not None:
                    death_checks += 1
                    near_death_zero += following == 0
        if damage_total < 3:
            continue
        damage_ratio = damage_match / damage_total
        healing_ratio = healing_match / healing_total if healing_total else 0.0
        death_ratio = near_death_zero / death_checks if death_checks else 0.0
        variation_ratio = changed / max(1, sample_count)
        score = (
            damage_ratio * 5.0
            + healing_ratio * 2.0
            + death_ratio * 4.0
            + min(1.0, variation_ratio)
            + min(1.0, len(by_actor) / max(1, len(players)))
        )
        rankings.append(
            (
                score,
                f"code={code.hex(' ')} score={score:.2f} actors={len(by_actor)} "
                f"samples={sample_count} changed={changed} range={minimum}..{maximum} "
                f"damage_sign={damage_match}/{damage_total} "
                f"healing_sign={healing_match}/{healing_total} "
                f"death_zero={near_death_zero}/{death_checks} zeros={zero_count}",
            )
        )

    print(f"capture={path.name} players={len(players)} candidate_codes={len(rankings)}")
    for _score, text in sorted(rankings, reverse=True)[:100]:
        print(text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    analyze(args.capture, args.data_root)


if __name__ == "__main__":
    main()
