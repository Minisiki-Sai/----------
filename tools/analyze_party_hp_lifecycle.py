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
    rows: list[tuple[object, list[object]]] = []
    players: set[int] = set()
    lifecycle: dict[int, list[tuple[float, bool]]] = defaultdict(list)
    deltas: dict[int, list[tuple[float, int, str]]] = defaultdict(list)

    for packet in packets:
        events = decoder.feed_events(packet)
        rows.append((packet, events))
        for event in events:
            if isinstance(event, PartyMemberEvent):
                try:
                    players.add(int(event.actor_id, 16))
                except ValueError:
                    pass
            elif isinstance(event, PlayerLifecycleEvent):
                actor_id = int(event.target_id, 16)
                players.add(actor_id)
                lifecycle[actor_id].append((first + event.timestamp_ms / 1000.0, event.active))
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

    hp_states: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for packet, _events in rows:
        if packet.inferred_direction() != "server_to_client":
            continue
        _phase, body = _phase_and_body(packet.payload, players)
        for actor_id in players:
            marker = actor_id.to_bytes(4, "little") + b"\x1f\x04\x00"
            offset = body.find(marker, 9)
            while offset >= 0:
                end = offset + len(marker) + 4
                if end <= len(body):
                    hp_states[actor_id].append(
                        (packet.timestamp_seconds, int.from_bytes(body[end - 4 : end], "little"))
                    )
                offset = body.find(marker, offset + 1)

    print(f"capture={path.name} players={len(players)}")
    for actor_id in sorted(lifecycle):
        states = sorted(set(hp_states[actor_id]))
        actor_deltas = sorted(deltas[actor_id])
        print(f"\nactor=0x{actor_id:08X} lifecycle={len(lifecycle[actor_id])} hp_states={len(states)}")
        for timestamp, active in lifecycle[actor_id]:
            if active:
                continue
            before = [row for row in states if timestamp - 15.0 <= row[0] <= timestamp]
            after = [row for row in states if timestamp < row[0] <= timestamp + 15.0]
            local_deltas = [row for row in actor_deltas if timestamp - 15.0 <= row[0] <= timestamp + 15.0]
            print(f"  death={timestamp-first:.3f}s absolute={timestamp:.3f}")
            print("    states=" + ", ".join(
                f"{time-first:.3f}:{value}" for time, value in [*before[-4:], *after[:4]]
            ))
            print("    deltas=" + ", ".join(
                f"{time-first:.3f}:{kind}:{value:+d}" for time, value, kind in local_deltas
            ))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    analyze(args.capture, args.data_root)


if __name__ == "__main__":
    main()
