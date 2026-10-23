from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("DRAGON_NEST_APP_VERSION", "4.20")

from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.v410_native_core import NativeSimulatorCore


def main() -> None:
    capture = Path(sys.argv[1]).resolve()
    output = Path(sys.argv[2]).resolve()
    store = DataStore.load()
    core = NativeSimulatorCore(store, BuildCalculator(store))
    core._queue_replay_score_sync = lambda *args, **kwargs: None

    def progress(percent: int | float, stage: str, detail: str = "") -> None:
        print(f"[{float(percent):6.1f}%] {stage} {detail}", flush=True)

    replay = core.load_replay(str(capture), progress)
    result: dict[str, object] = {"replay": replay}
    score = core.score_replay(
        {
            "sourceHash": replay["sourceHash"],
            "sourceFile": str(capture),
            "bossTargetIds": [],
            "bossPreset": "红龙佩达",
            "glyphOverrides": {},
        },
        progress,
    )
    result["score"] = score
    details: list[dict[str, object]] = []
    for row in score.get("rows", []):
        detail = core.score_detail(
            str(score["scoreKey"]),
            str(row["sourceId"]),
            str(replay["sourceHash"]),
        )
        details.append(detail)
    result["details"] = details
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"WROTE {output}")


if __name__ == "__main__":
    main()
