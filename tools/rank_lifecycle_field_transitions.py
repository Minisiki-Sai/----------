from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


DEFAULT_BANDS = {
    "death": (1853.20, 1853.80),
    "revive": (1897.00, 1897.80),
}


def _layout_name(layout: dict[str, Any]) -> str:
    return (
        f"{layout['transport']}:{layout['direction']}:len={layout['length']}:"
        f"sig={layout['signature']}:phase={layout['xor_phase']}:off={layout['actor_offset']}"
    )


def _constant_positions(rows: list[dict[str, Any]]) -> dict[int, int]:
    if not rows:
        return {}
    width = min(len(bytes.fromhex(row["after_hex"])) for row in rows)
    result: dict[int, int] = {}
    for pos in range(width):
        values = {bytes.fromhex(row["after_hex"])[pos] for row in rows}
        if len(values) == 1:
            result[pos] = next(iter(values))
    return result


def _actor_modes(rows: list[dict[str, Any]], position: int) -> dict[str, int]:
    values: dict[str, Counter[int]] = defaultdict(Counter)
    for row in rows:
        payload = bytes.fromhex(row["after_hex"])
        if position < len(payload):
            values[row["actor_id"]][payload[position]] += 1
    return {actor: counts.most_common(1)[0][0] for actor, counts in values.items()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--limit", type=int, default=60)
    args = parser.parse_args()

    source = json.loads(args.report.read_text(encoding="utf-8"))
    ranked: list[dict[str, Any]] = []
    for layout in source["layouts"]:
        matches = layout["matches"]
        band_rows: dict[str, list[dict[str, Any]]] = {}
        for label, (start, end) in DEFAULT_BANDS.items():
            band_rows[label] = [row for row in matches if start <= float(row["seconds"]) <= end]
        death_rows = band_rows["death"]
        revive_rows = band_rows["revive"]
        death_actors = {row["actor_id"] for row in death_rows}
        revive_actors = {row["actor_id"] for row in revive_rows}
        common_actors = death_actors & revive_actors
        if len(common_actors) < 2:
            continue

        death_constants = _constant_positions(death_rows)
        revive_constants = _constant_positions(revive_rows)
        constant_flips = [
            (pos, death_constants[pos], revive_constants[pos])
            for pos in death_constants.keys() & revive_constants.keys()
            if death_constants[pos] != revive_constants[pos]
        ]

        width = min(
            min(len(bytes.fromhex(row["after_hex"])) for row in death_rows),
            min(len(bytes.fromhex(row["after_hex"])) for row in revive_rows),
        )
        actor_flips: list[dict[str, Any]] = []
        for pos in range(width):
            death_modes = _actor_modes(death_rows, pos)
            revive_modes = _actor_modes(revive_rows, pos)
            actors = common_actors & death_modes.keys() & revive_modes.keys()
            changed = [actor for actor in actors if death_modes[actor] != revive_modes[actor]]
            zero_to_nonzero = [
                actor for actor in actors if death_modes[actor] == 0 and revive_modes[actor] != 0
            ]
            nonzero_to_zero = [
                actor for actor in actors if death_modes[actor] != 0 and revive_modes[actor] == 0
            ]
            if changed:
                actor_flips.append(
                    {
                        "position": pos,
                        "changed": len(changed),
                        "actors": len(actors),
                        "zero_to_nonzero": len(zero_to_nonzero),
                        "nonzero_to_zero": len(nonzero_to_zero),
                        "death_modes": death_modes,
                        "revive_modes": revive_modes,
                    }
                )
        actor_flips.sort(
            key=lambda item: (
                item["zero_to_nonzero"],
                item["changed"],
                -item["nonzero_to_zero"],
            ),
            reverse=True,
        )
        score = (
            20 * len(common_actors)
            + 8 * len(constant_flips)
            + sum(item["zero_to_nonzero"] * 5 + item["changed"] for item in actor_flips[:4])
        )
        ranked.append(
            {
                "score": score,
                "layout": _layout_name(layout),
                "death_rows": len(death_rows),
                "revive_rows": len(revive_rows),
                "death_actors": sorted(death_actors),
                "revive_actors": sorted(revive_actors),
                "common_actor_count": len(common_actors),
                "constant_flips": constant_flips,
                "top_actor_flips": actor_flips[:8],
                "death_samples": [
                    {
                        "seconds": row["seconds"],
                        "actor_id": row["actor_id"],
                        "after_hex": row["after_hex"],
                    }
                    for row in death_rows[:12]
                ],
                "revive_samples": [
                    {
                        "seconds": row["seconds"],
                        "actor_id": row["actor_id"],
                        "after_hex": row["after_hex"],
                    }
                    for row in revive_rows[:12]
                ],
            }
        )

    ranked.sort(key=lambda item: item["score"], reverse=True)
    output = {"bands": DEFAULT_BANDS, "candidates": ranked[: args.limit]}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
