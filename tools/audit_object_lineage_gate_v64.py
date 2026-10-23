"""Read-only trace of one damage-object generation at its direct37 Hit."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--owner", type=lambda value: int(value, 0), required=True)
    parser.add_argument("--object", type=lambda value: int(value, 0), required=True)
    parser.add_argument("--damage", type=int, default=0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    source = V576DiagnosticReplaySource(args.capture, DATA_DIR, {})
    decoder = source._decoder
    ledger = decoder.damage_object_origins
    original_registration = ledger.observe_registration
    original_attribution = ledger.attribute_direct37
    rows: list[dict[str, object]] = []

    def observe_registration(**kwargs):
        result = original_registration(**kwargs)
        if (
            int(kwargs.get("source_actor_id", 0)) == args.owner
            and int(kwargs.get("damage_object_id", 0)) == args.object
        ):
            rows.append({
                "kind": "registration",
                "kwargs": kwargs,
                "result": asdict(result) if result is not None else None,
                "stored": asdict(ledger._origins.get((args.owner, args.object)))
                if ledger._origins.get((args.owner, args.object)) is not None else None,
            })
        return result

    def attribute_direct37(**kwargs):
        result = original_attribution(**kwargs)
        if (
            int(kwargs.get("source_actor_id", 0)) == args.owner
            and (not args.damage or int(kwargs.get("damage", 0)) == args.damage)
        ):
            origin = ledger._origins.get((args.owner, args.object))
            if origin is not None:
                rows.append({
                    "kind": "hit",
                    "kwargs": {key: value for key, value in kwargs.items() if key != "payload"},
                    "origin": asdict(origin),
                    "result": asdict(result) if result is not None else None,
                    "deterministic": bool(result and result.deterministically_validated),
                })
        return result

    ledger.observe_registration = observe_registration
    ledger.attribute_direct37 = attribute_direct37
    try:
        source.poll()
    finally:
        source.close()
    payload = {"capture": str(args.capture), "owner": args.owner, "object": args.object, "rows": rows}
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
