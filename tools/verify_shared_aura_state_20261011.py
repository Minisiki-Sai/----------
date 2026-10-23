"""Read-only replay: compare live, score valuation context and upload lifetimes."""
from dataclasses import asdict
from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.compare_aura_coverage_20261011 import replay, coverage
from dragon_nest_builder.live_dps.aura_state import is_aura_damage_series
from dragon_nest_builder.live_dps.scoring import TeamScoreEngine, DataStore
from dragon_nest_builder.live_dps.event_platform import event_platform_for
from dragon_nest_builder.live_dps.compact_score_timeline import build_compact_score_timeline
from dragon_nest_builder.live_dps.score_timeline_display import score_timeline_display_context
from dragon_nest_builder.v410_native_core import NativeSimulatorCore


def audit(path):
    snapshot, meta = replay(path)
    owner = "0x5054AEFC"
    raw = [asdict(row) for row in snapshot.player_buff_series]
    core = object.__new__(NativeSimulatorCore)
    core._live_buff_catalog = {"判定光环": {"display": "光攻增益", "scope": "team"}}
    display = core._with_live_display_projection({
        "elapsed_seconds": snapshot.elapsed_seconds, "player_buff_series": raw,
        "boss_effect_current_seconds": snapshot.boss_effect_current_seconds,
        "player_buffs": [asdict(row) for row in snapshot.player_buffs],
    })
    windows = TeamScoreEngine._output_windows(snapshot)
    score = coverage(snapshot, windows, False)
    own_score = next(row for row in score[owner + ":圣骑互斥光环"]["recipients"] if row["id"] == owner)
    packet = [row for row in raw if row["target_id"] == row["source_id"] == owner
              and row["skill_id"] == "3213" and is_aura_damage_series(row)]
    live = [row for row in display["player_buff_series"]
            if row["target_id"] == row["source_id"] == owner and row["skill_id"] == "3213"]
    overview = [row for row in display["player_buffs"]
                if row["target_id"] == row["source_id"] == owner and row["skill_id"] == "3213"]
    detail = {"source_id": owner, "_compact_duration_seconds": max(snapshot.elapsed_seconds, snapshot.boss_effect_current_seconds),
              "_compact_player_buffs": [row for row in raw if row["target_id"] == owner]}
    upload = build_compact_score_timeline(detail)
    starts = 0
    bars = []
    for row in upload.get("e", []):
        starts += row[0]
        name = upload["n"][row[2]]
        if "判定光环" in name:
            bars.append((starts / upload["q"], (starts + row[1]) / upload["q"], name))
    render = score_timeline_display_context(detail, event_platform_for(snapshot), (), (), ())
    history_rows = [row for row in render["snapshot"]["player_buff_series"]
                    if row["skill_id"] == "3213" and row["source_id"] == "self"]
    received = packet[0]["intervals"]
    assert received == tuple(map(tuple, live[0]["intervals"]))
    assert received == own_score["intervals"]
    assert received == history_rows[0]["intervals"]
    assert not [row for row in snapshot.boss_effect_events if row.target_id == owner]
    # Compact format rounds to centiseconds, but must not change the interval.
    assert any(abs(a-received[0][0]) <= .01 and abs(b-received[0][1]) <= .01 for a, b, _ in bars), (received, bars)
    return {**meta, "elapsed_seconds": snapshot.elapsed_seconds,
            "record_clock_seconds": snapshot.boss_effect_current_seconds,
            "shared_intervals_verified": True, "player_misclassified_boss_status_count": 0,
            "packet_own_aura": packet, "live_own_aura": live,
            "live_own_overview": overview, "support_and_standard_damage_context": own_score,
            "history_render_own_aura": history_rows, "compact_upload_bars": bars,
            "team_score_time_coverage_percent": score[owner + ":圣骑互斥光环"]["score_time_coverage_percent"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    print(json.dumps(audit(parser.parse_args().capture), ensure_ascii=False), flush=True)
