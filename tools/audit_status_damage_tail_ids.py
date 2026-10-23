from __future__ import annotations

import argparse
import json
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.live_dps.protocol_v576 import (
    decode_captured_frame,
    decode_v576_status_damage_events,
    split_v576_payload,
)


HEADER = struct.Struct("<dBII")


def frames(path: Path):
    with path.open("rb") as stream:
        if stream.read(6) != b"DNDP1\0":
            raise ValueError("not a DNDP1 capture")
        while header := stream.read(HEADER.size):
            timestamp, adapter, original, size = HEADER.unpack(header)
            raw = stream.read(size)
            if len(raw) != size:
                break
            yield timestamp, adapter, original, raw


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    counts = Counter()
    damage = Counter()
    amounts: dict[tuple[str, str], Counter[int]] = defaultdict(Counter)
    first = None
    examples: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for timestamp, adapter, original, raw in frames(args.capture):
        first = timestamp if first is None else first
        transport = decode_captured_frame(timestamp, raw, original)
        if transport is None or transport.transport != "UDP":
            continue
        for wire in split_v576_payload(transport.payload):
            for row in decode_v576_status_damage_events(
                wire, transport.inferred_direction()
            ):
                source = f"0x{int(row.source_actor_id or 0):08X}"
                skill = str(row.skill_id if row.skill_id is not None else "none")
                key = (source, skill)
                counts[key] += 1
                damage[key] += int(row.damage or 0)
                amounts[key][int(row.damage or 0)] += 1
                if len(examples[key]) < 10:
                    examples[key].append(
                        {
                            "t": round(timestamp - first, 3),
                            "damage": row.damage,
                            "target": f"0x{int(row.target_actor_id or 0):08X}",
                            "layout": row.layout,
                        }
                    )
    result = {
        "capture": str(args.capture),
        "rows": [
            {
                "source": source,
                "tailSkillId": skill,
                "hits": hits,
                "damage": damage[(source, skill)],
                "amounts": [
                    {"amount": amount, "hits": amount_hits}
                    for amount, amount_hits in amounts[(source, skill)].most_common(20)
                ],
                "examples": examples[(source, skill)],
            }
            for (source, skill), hits in counts.most_common()
        ],
    }
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
