from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audit_cast_damage_tokens import audit_capture, _latest_matching_action


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--skill", type=int, required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit_capture(args.capture.resolve())
    actions = sorted(result["actions"], key=lambda row: row.timestamp)
    rows = []
    for damage in result["exact_damage"]:
        if damage.skill_id != args.skill:
            continue
        action = _latest_matching_action(actions, damage)
        if action is None:
            continue
        rows.append(
            {
                "delta_ms": round(
                    (damage.timestamp - action.timestamp) * 1000.0, 3
                ),
                "action_layout": action.layout,
                "action_signature": action.signature,
                "action_length": action.payload_length,
                "action_marker_offset": action.marker_offset,
                "action_hex": action.body.hex(" "),
                "damage_layout": damage.layout,
                "damage_signature": damage.signature,
                "damage_length": damage.payload_length,
                "damage_marker_offset": damage.marker_offset,
                "result_group_id": int.from_bytes(
                    damage.body[
                        damage.marker_offset + 4 : damage.marker_offset + 8
                    ],
                    "little",
                ),
                "damage_object_id": int.from_bytes(
                    damage.body[
                        damage.marker_offset + 13 : damage.marker_offset + 17
                    ],
                    "little",
                ),
                "damage_hex": damage.body.hex(" "),
            }
        )
        if len(rows) >= args.limit:
            break
    text = json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
