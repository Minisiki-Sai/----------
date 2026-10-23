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


HP_MARKER = b"\x1f\x04\x00"


def analyze(path: Path, data_root: Path) -> None:
    packets = list(_iter_packets(path))
    decoder = _V576PacketEventDecoder(data_root)
    first = packets[0].timestamp_seconds if packets else 0.0
    raw: list[tuple[object, list[object]]] = []
    player_ids: set[int] = set()
    deltas: dict[int, list[tuple[float, int, str]]] = defaultdict(list)
    lifecycle: dict[int, list[tuple[float, bool]]] = defaultdict(list)
    for packet in packets:
        events = decoder.feed_events(packet)
        raw.append((packet, events))
        for event in events:
            if isinstance(event, PartyMemberEvent):
                try:
                    player_ids.add(int(event.actor_id, 16))
                except ValueError:
                    pass
            elif isinstance(event, PlayerLifecycleEvent):
                try:
                    lifecycle[int(event.target_id, 16)].append(
                        (first + event.timestamp_ms / 1000.0, event.active)
                    )
                except ValueError:
                    pass
            if not isinstance(event, CombatEvent) or not event.target_id:
                continue
            try:
                target_id = int(event.target_id, 16)
            except ValueError:
                continue
            if event.attribution_method == "incoming_damage":
                player_ids.add(target_id)
                deltas[target_id].append(
                    (first + event.timestamp_ms / 1000.0, -event.amount, "damage")
                )
            elif event.healing and target_id in player_ids:
                deltas[target_id].append(
                    (first + event.timestamp_ms / 1000.0, event.amount, "healing")
                )

    states: dict[int, list[tuple[float, int, str]]] = defaultdict(list)
    for packet, _events in raw:
        if packet.inferred_direction() != "server_to_client":
            continue
        phase, body = _phase_and_body(packet.payload, player_ids)
        signature = packet.payload[5:9].hex() if len(packet.payload) >= 9 else ""
        for actor_id in player_ids:
            pattern = actor_id.to_bytes(4, "little") + HP_MARKER
            offset = body.find(pattern, 9)
            while offset >= 0:
                value_offset = offset + len(pattern)
                if value_offset + 4 <= len(body):
                    value = int.from_bytes(body[value_offset : value_offset + 4], "little")
                    states[actor_id].append(
                        (packet.timestamp_seconds, value, f"{signature}/{len(body)}/p{phase}")
                    )
                offset = body.find(pattern, offset + 1)

    print(f"capture={path.name} players={len(player_ids)}")
    print("1F04 observations by actor:")
    total_rows = 0
    total_changes = 0
    exact_matches = 0
    near_matches = 0
    comparison_rows: list[tuple[float, str]] = []
    event_comparison_rows: list[tuple[float, str]] = []
    damage_sign_total = 0
    damage_sign_match = 0
    healing_sign_total = 0
    healing_sign_match = 0
    for actor_id in sorted(player_ids):
        actor_states = sorted(set(states[actor_id]))
        actor_deltas = sorted(deltas[actor_id])
        total_rows += len(actor_states)
        changes = sum(a[1] != b[1] for a, b in zip(actor_states, actor_states[1:]))
        total_changes += changes
        print(
            f"  0x{actor_id:08X}: states={len(actor_states)} changes={changes} "
            f"damage_events={sum(kind == 'damage' for _t, _v, kind in actor_deltas)} "
            f"healing_events={sum(kind == 'healing' for _t, _v, kind in actor_deltas)} "
            f"min={min((row[1] for row in actor_states), default=0)} "
            f"max={max((row[1] for row in actor_states), default=0)} "
            f"zeros={sum(row[1] == 0 for row in actor_states)}"
        )
        for previous, current in zip(actor_states, actor_states[1:]):
            if current[1] == previous[1]:
                continue
            wire_delta = current[1] - previous[1]
            interval = [
                row for row in actor_deltas if previous[0] < row[0] <= current[0] + 0.001
            ]
            event_delta = sum(value for _time, value, _kind in interval)
            residual = wire_delta - event_delta
            if wire_delta == event_delta:
                exact_matches += 1
            tolerance = max(500, round(abs(event_delta) * 0.02))
            if abs(residual) <= tolerance:
                near_matches += 1
            detail = ",".join(
                f"{kind}:{value:+d}@{time-first:.3f}"
                for time, value, kind in interval[:6]
            )
            comparison_rows.append(
                (
                    abs(residual),
                    f"    0x{actor_id:08X} {previous[0]-first:.3f}->{current[0]-first:.3f}s "
                    f"wire={previous[1]}->{current[1]} ({wire_delta:+d}) "
                    f"events={event_delta:+d} residual={residual:+d} "
                    f"[{detail}] {current[2]}",
                )
            )
            if interval:
                event_comparison_rows.append(comparison_rows[-1])
                kinds = {kind for _time, _value, kind in interval}
                if kinds == {"damage"}:
                    damage_sign_total += 1
                    damage_sign_match += wire_delta < 0
                elif kinds == {"healing"}:
                    healing_sign_total += 1
                    healing_sign_match += wire_delta > 0

    print(
        f"summary states={total_rows} changes={total_changes} "
        f"exact_delta_matches={exact_matches} near_delta_matches={near_matches}"
    )
    print(
        f"sign agreement: damage={damage_sign_match}/{damage_sign_total} "
        f"healing={healing_sign_match}/{healing_sign_total}"
    )
    print("Lifecycle edges and nearest 1F04 samples:")
    for actor_id, edges in sorted(lifecycle.items()):
        actor_states = sorted(set(states[actor_id]))
        for timestamp, active in edges:
            before = next((row for row in reversed(actor_states) if row[0] <= timestamp), None)
            after = next((row for row in actor_states if row[0] >= timestamp), None)
            print(
                f"  0x{actor_id:08X} t={timestamp-first:.3f}s active={int(active)} "
                f"before={before[1] if before else '-'} after={after[1] if after else '-'}"
            )
    print("Closest changed-state/event-delta comparisons:")
    for _residual, text in sorted(comparison_rows, key=lambda row: row[0])[:40]:
        print(text)
    print("Changed states whose interval contains decoded damage/healing:")
    for _residual, text in sorted(event_comparison_rows, key=lambda row: row[0])[:60]:
        print(text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    analyze(args.capture, args.data_root)


if __name__ == "__main__":
    main()
