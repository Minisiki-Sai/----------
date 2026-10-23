from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR, DataStore
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.combat_glyph_inference import (
    combat_glyph_signature,
    infer_combat_skill_glyphs,
)
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource


def inspect_capture(path: Path, store: DataStore, repeats: int = 1) -> dict[str, object]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    aggregator = CombatAggregator()
    try:
        for batch in source.iter_batches():
            for event in batch:
                aggregator.ingest(event)
    finally:
        source.close()
    snapshot = aggregator.snapshot(include_events=True)
    timings, cooldown_timings = [], []
    previous_signature = None
    for _ in range(max(1, repeats)):
        rows_by_source, diagnostics = infer_combat_skill_glyphs(snapshot, store)
        signature = combat_glyph_signature(rows_by_source)
        if previous_signature is not None and signature != previous_signature:
            raise RuntimeError("Repeated glyph inference changed on the same snapshot")
        previous_signature = signature
        timings.append(diagnostics["elapsed_ms"])
        cooldown_timings.append(diagnostics["cooldown_analysis_ms"])
    return {
        "capture": str(path.resolve()),
        "elapsed_seconds": snapshot.elapsed_seconds,
        "inference_timings_ms": timings,
        "cooldown_timings_ms": cooldown_timings,
        "players": [
            {
                "source_id": player.source_id,
                "name": player.name,
                "job": player.job,
                "glyphs": rows_by_source.get(player.source_id, []),
            }
            for player in snapshot.players
        ],
        "diagnostics": diagnostics,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Infer equipped skill glyphs from combat traffic only."
    )
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()
    store = DataStore.load()
    rows = [
        inspect_capture(path.expanduser().resolve(), store, args.repeat)
        for path in args.captures
    ]
    payload = {"captures": rows}
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
        print(args.output.resolve())
    else:
        print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
