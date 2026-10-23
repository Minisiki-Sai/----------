"""Read-only reproduction of per-entity status table versus curve discrepancy."""
import json
import sys
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps.capture_sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.v410_native_core import _boss_effect_views_payload
from dragon_nest_builder.live_dps.boss_debuff_archive import encode_boss_debuff_archive


def main():
    path = Path(sys.argv[1])
    started = time.perf_counter()
    source = V576DiagnosticReplaySource(path, ROOT / "dragon_nest_builder/data")
    try:
        events = source.poll()
        integrity = source.capture_integrity
    finally:
        source.close()
    print(f"decoded {len(events)} events in {time.perf_counter()-started:.1f}s", flush=True)
    agg = CombatAggregator()
    for event in events:
        agg.ingest(event)
    snapshot = agg.snapshot()
    views = _boss_effect_views_payload(snapshot, agg)
    current_ms = round(max(snapshot.elapsed_seconds, snapshot.boss_effect_current_seconds) * 1000)
    report = dict(capture=path.name, integrity=integrity,
                  summary_rows=len(snapshot.boss_effects),
                  detail_rows=len(snapshot.boss_effect_events), entities=[])
    report["ui"] = dict(
        effects=[asdict(r) for r in snapshot.boss_effects],
        events=[asdict(r) for r in snapshot.boss_effect_events],
        views=views, series=snapshot.boss_effect_series, extent=current_ms / 1000,
    )
    report["archive"] = encode_boss_debuff_archive(snapshot, views)
    for view in views:
        ids = frozenset(view["target_ids"])
        exact = agg.boss_effect_view(ids, current_ms)
        selected = [row for row in snapshot.boss_effects if row.target_id in ids]
        detail = [row for row in snapshot.boss_effect_events if row.target_id in ids]
        row = dict(name=view["name"], target_ids=sorted(ids),
                   identity=[agg._target_identity_key(t, agg._actor_context.get(t, ("", ""))[0],
                             agg._target_max_hp.get(t, 0)) for t in sorted(ids)],
                   table_rows=[asdict(r) for r in selected],
                   exact_rows=[asdict(r) for r in exact.effects],
                   detail_rows=len(detail), detail_skills=dict(Counter(r.skill_name for r in detail)),
                   curve_max={key:max((value for _, value in points), default=0)
                              for key, points in exact.series},
                   output_windows=view["output_windows"])
        report["entities"].append(row)
        print(json.dumps(dict(name=row["name"], ids=row["target_ids"], table=len(selected),
                              exact=len(exact.effects), detail=len(detail),
                              skills=sorted(set(r.skill_name for r in exact.effects))),
                         ensure_ascii=False), flush=True)
    out = ROOT / "outputs/verification/boss_status_entity_20261020"
    out.mkdir(parents=True, exist_ok=True)
    filename = sys.argv[2] if len(sys.argv) > 2 else path.stem + ".json"
    (out / filename).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
