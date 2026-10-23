from __future__ import annotations

import argparse
from pathlib import Path

from dragon_nest_builder.data_loader import DATA_DIR, DataStore
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.boss_recognizer import BossRecognizer
from dragon_nest_builder.live_dps.replay_cache import (
    ReplayDiskCache,
    replay_cache_identity,
)
from dragon_nest_builder.live_dps.scoring import (
    TeamScoreEngine,
    _apply_survival_penalty_cap,
    _survival_discipline_detail,
    role_for_job,
)
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource


def load_snapshot(path: Path) -> object:
    identity = replay_cache_identity(path, DATA_DIR, {}, parser_mode="structured")
    cache = ReplayDiskCache()
    aggregator = cache.load(identity)
    if aggregator is None:
        source = V576DiagnosticReplaySource(path, DATA_DIR)
        aggregator = CombatAggregator()
        for event in source.poll():
            aggregator.ingest(event)
        cache.save(identity, aggregator)
    return aggregator.snapshot()


def compare_capture(path: Path) -> list[dict[str, object]]:
    snapshot = load_snapshot(path)
    encounters = tuple(BossRecognizer().recognize_snapshot(snapshot))
    target_ids = frozenset(
        target_id
        for encounter in encounters
        for target_id in encounter.target_ids
    ) or None
    engine = TeamScoreEngine(DataStore.load())
    windows = engine._output_windows(snapshot, target_ids)
    tracks = {
        str(track.target_id): track
        for track in getattr(snapshot, "player_health_tracks", ())
    }
    rows: list[dict[str, object]] = []
    for player in snapshot.players:
        role = role_for_job(player.job)
        track = tracks.get(str(player.source_id))
        detail = _survival_discipline_detail(
            snapshot,
            player.source_id,
            windows,
            role,
            standard_hp=float(getattr(track, "max_hp", 0.0) if track else 0.0),
        )
        detail = _apply_survival_penalty_cap(detail, 100.0, role)
        shadow = detail.hp_shadow
        rows.append(
            {
                "record": path.stem,
                "source_id": player.source_id,
                "job": player.job,
                "role": role,
                "formal_penalty": round(detail.penalty, 3),
                "formal_damage_penalty": round(detail.damage_penalty, 3),
                "formal_incoming": detail.incoming_damage,
                "formal_avoidable_hp_per_min": round(
                    detail.avoidable_hp_loss_per_minute, 5
                ),
                "hp_shadow_available": shadow.available,
                "hp_shadow_penalty": round(shadow.penalty, 3),
                "hp_shadow_damage_penalty": round(shadow.damage_penalty, 3),
                "hp_snapshot_loss": round(shadow.incoming_hp_loss),
                "hp_shadow_avoidable_hp_per_min": round(
                    shadow.avoidable_hp_loss_per_minute, 5
                ),
                "hp_shadow_team": shadow.team_mechanic_incident_count,
                "hp_shadow_targeted": shadow.targeted_mechanic_incident_count,
                "hp_shadow_avoidable": shadow.avoidable_incident_count,
                "hp_shadow_uncertain": shadow.uncertain_incident_count,
                "death_count": detail.death_count,
                "hp_points": shadow.point_count,
                "hp_max": round(shadow.max_hp),
                "hp_confidence": shadow.confidence,
            }
        )
    return rows


def write_tsv(rows: list[dict[str, object]], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0]) if rows else []
    lines = ["\t".join(columns)]
    lines.extend("\t".join(str(row[column]) for column in columns) for row in rows)
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = [
        row
        for capture in args.captures
        for row in compare_capture(capture.resolve())
    ]
    write_tsv(rows, args.output.resolve())
    for row in rows:
        print(
            row["record"],
            row["job"],
            f"formal={row['formal_penalty']}",
            f"hp_shadow={row['hp_shadow_penalty']}",
            f"loss={row['hp_snapshot_loss']}",
            f"events={row['hp_shadow_team']}/"
            f"{row['hp_shadow_targeted']}/"
            f"{row['hp_shadow_avoidable']}/"
            f"{row['hp_shadow_uncertain']}",
        )


if __name__ == "__main__":
    main()
