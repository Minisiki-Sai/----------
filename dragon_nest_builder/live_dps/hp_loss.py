from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from .models import CombatEvent, PlayerHealthTrackSnapshot


_MAX_SAMPLE_GAP_SECONDS = 30.0
_NOISE_RATIO = 0.001


@dataclass(frozen=True, slots=True)
class ReconstructedHpLoss:
    """One effective HP loss admitted by adjacent server HP snapshots."""

    timestamp_seconds: float
    target_id: str
    amount: float
    max_hp: float


def reconstruct_player_hp_losses(
    tracks: Iterable[PlayerHealthTrackSnapshot],
    incoming_damage_events: Iterable[CombatEvent],
    combat_events: Iterable[CombatEvent],
    *,
    evaluation_windows: Iterable[tuple[float, float]] | None = None,
) -> tuple[tuple[ReconstructedHpLoss, ...], frozenset[str]]:
    """Rebuild effective incoming damage from the authoritative HP stream.

    Decoded damage and healing packets are replayed between two adjacent HP
    snapshots.  The ending HP snapshot is authoritative, so an unexplained
    negative residual is retained as real damage that the packet decoder did
    not see.  Overkill is capped by the HP available at the time of the hit.

    The returned target set contains players with at least one usable adjacent
    snapshot pair.  Callers can safely fall back to the packet ledger for all
    other players.
    """

    track_by_target = {
        str(track.target_id): track
        for track in tracks
        if str(track.target_id)
    }
    windows = tuple(evaluation_windows or ())

    def in_same_window(left: float, right: float) -> bool:
        if not windows:
            return True
        return any(
            start - 1e-9 <= left <= end + 1e-9
            and start - 1e-9 <= right <= end + 1e-9
            for start, end in windows
        )

    damage_by_target: dict[str, list[CombatEvent]] = defaultdict(list)
    for event in incoming_damage_events:
        target_id = str(event.target_id)
        if target_id in track_by_target and event.amount > 0:
            damage_by_target[target_id].append(event)
    healing_by_target: dict[str, list[CombatEvent]] = defaultdict(list)
    for event in combat_events:
        target_id = str(event.target_id)
        if event.healing and target_id in track_by_target and event.amount > 0:
            healing_by_target[target_id].append(event)
    for rows in (*damage_by_target.values(), *healing_by_target.values()):
        rows.sort(key=lambda event: event.timestamp_ms)

    losses: list[ReconstructedHpLoss] = []
    available_targets: set[str] = set()
    for target_id, track in track_by_target.items():
        maximum = max(0.0, float(track.max_hp))
        points = tuple(
            sorted(track.points, key=lambda point: point.timestamp_seconds)
        )
        if maximum <= 0 or len(points) < 2:
            continue
        noise_floor = max(100.0, maximum * _NOISE_RATIO)
        target_state_events = sorted(
            (
                event.timestamp_ms / 1000.0,
                (
                    "heal"
                    if event.healing
                    else "self_loss"
                    if str(event.source_id)
                    and str(event.source_id) == str(event.target_id)
                    else "damage"
                ),
                max(0.0, float(event.amount)),
            )
            for event in (
                *damage_by_target.get(target_id, ()),
                *healing_by_target.get(target_id, ()),
            )
        )
        event_cursor = 0
        for previous, current in zip(points, points[1:]):
            previous_seconds = float(previous.timestamp_seconds)
            current_seconds = float(current.timestamp_seconds)
            if (
                current_seconds <= previous_seconds
                or current_seconds - previous_seconds > _MAX_SAMPLE_GAP_SECONDS
                or not in_same_window(previous_seconds, current_seconds)
            ):
                continue
            available_targets.add(target_id)
            ledger_hp = max(0.0, min(maximum, float(previous.current_hp)))
            while (
                event_cursor < len(target_state_events)
                and target_state_events[event_cursor][0] <= previous_seconds
            ):
                event_cursor += 1
            interval_cursor = event_cursor
            while (
                interval_cursor < len(target_state_events)
                and target_state_events[interval_cursor][0] <= current_seconds
            ):
                event_seconds, kind, amount = target_state_events[interval_cursor]
                interval_cursor += 1
                if amount <= 0:
                    continue
                if kind == "heal":
                    ledger_hp = min(maximum, ledger_hp + amount)
                    continue
                actual_loss = min(ledger_hp, amount)
                if actual_loss <= 0:
                    continue
                ledger_hp -= actual_loss
                if kind == "self_loss":
                    # Self-sourced resource costs still move the HP ledger,
                    # but they are life loss rather than incoming damage.
                    continue
                losses.append(
                    ReconstructedHpLoss(
                        event_seconds,
                        target_id,
                        actual_loss,
                        maximum,
                    )
                )
            event_cursor = interval_cursor

            authoritative_hp = max(
                0.0,
                min(maximum, float(current.current_hp)),
            )
            residual_loss = max(0.0, ledger_hp - authoritative_hp)
            if residual_loss >= noise_floor:
                losses.append(
                    ReconstructedHpLoss(
                        current_seconds,
                        target_id,
                        residual_loss,
                        maximum,
                    )
                )

    losses.sort(key=lambda row: (row.timestamp_seconds, row.target_id))
    return tuple(losses), frozenset(available_targets)


def reconstructed_hp_loss_totals(
    tracks: Iterable[PlayerHealthTrackSnapshot],
    incoming_damage_events: Iterable[CombatEvent],
    combat_events: Iterable[CombatEvent],
) -> tuple[dict[str, int], frozenset[str]]:
    losses, available_targets = reconstruct_player_hp_losses(
        tracks,
        incoming_damage_events,
        combat_events,
    )
    totals: dict[str, float] = defaultdict(float)
    for loss in losses:
        totals[loss.target_id] += loss.amount
    return (
        {target_id: int(round(amount)) for target_id, amount in totals.items()},
        available_targets,
    )
